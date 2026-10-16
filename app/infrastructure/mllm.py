"""vLLM 异步多模态客户端：页面引用、并发配额与缓存失效恢复。"""

from typing import Any, Literal

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.config.mllm import MLLMSettings, load_mllm_settings
from app.infrastructure.model_concurrency import get_mllm_request_limiter
from app.infrastructure.png_image import PNGImage, materialize_image_messages
from app.infrastructure.vllm_media_reference import (
    get_vllm_media_reference_coordinator, is_vllm_media_cache_miss,
    strip_media_reference_metadata,
)
from app.model.pdf import PreparedPDFPage


class MLLMRequestError(RuntimeError):
    """请求无效、认证失败或接口协议不匹配，不能当作业务拒绝。"""


class MLLMUnavailableError(RuntimeError):
    """模型连接、限流或服务端暂时失败。"""


def pdf_page_content(page: PreparedPDFPage) -> dict[str, Any]:
    """保留 PNG 内存引用，发送边界才转换为完整 data URL 或 UUID-only。"""
    return {
        "type": "image_url", "uuid": page.media_uuid,
        "image_url": {"url": PNGImage(png_bytes=page.png_bytes, content_sha256=page.content_sha256)},
    }


class MLLMClient:
    def __init__(self, settings: MLLMSettings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or load_mllm_settings()
        self._client = AsyncOpenAI(
            api_key=self.settings.api_key.get_secret_value() or "vllm-local",
            base_url=self.settings.api_base_url + "/",
            timeout=self.settings.timeout_seconds, max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, timeout=self.settings.timeout_seconds),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        await self._client.close()

    async def create_chat_completion(
        self, *, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int | None = None,
        tool_placement: Literal["before_task", "after_task"] | None = None,
        tool_task_index: int | None = None,
        preserve_thinking: bool | None = None,
    ) -> ChatCompletion:
        """返回 SDK 原始非流式响应，保留正文、工具调用、reasoning 和用量。"""
        if not messages:
            raise ValueError("MLLM 消息不能为空")
        generation = self.settings.generation
        maximum = generation.max_completion_tokens if max_completion_tokens is None else max_completion_tokens
        if type(maximum) is not int or not 0 < maximum <= generation.max_completion_tokens:
            raise ValueError("生成 token 数必须为正整数且不超过配置上限")
        template = {"enable_thinking": generation.enable_thinking}
        if preserve_thinking is not None:
            if type(preserve_thinking) is not bool:
                raise ValueError("历史思考保留开关必须为布尔值")
            template["preserve_thinking"] = preserve_thinking
        if tool_placement is not None or tool_task_index is not None:
            if (tool_placement not in {"before_task", "after_task"} or not tools
                    or type(tool_task_index) is not int or not 0 <= tool_task_index < len(messages)
                    or messages[tool_task_index].get("role") != "user"):
                raise ValueError("工具位置必须绑定有效的 user 任务消息并提供工具")
            template.update(tool_placement=tool_placement, tool_task_index=tool_task_index)
        if generation.enable_thinking:
            template["reasoning_effort"] = generation.reasoning_effort
        request = {
            "model": self.settings.model, "stream": False,
            "max_completion_tokens": maximum, "temperature": generation.temperature,
            "top_p": generation.top_p, "presence_penalty": generation.presence_penalty,
            "seed": generation.seed,
            "extra_body": {"top_k": generation.top_k, "repetition_penalty": generation.repetition_penalty,
                           "chat_template_kwargs": template},
        }
        if tools is not None:
            request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice
        if response_format is not None:
            request["response_format"] = response_format
        try:
            return await self._with_media_references(messages, request)
        except APIConnectionError as exc:
            raise MLLMUnavailableError("MLLM 连接失败或超时") from exc
        except APIStatusError as exc:
            status = exc.status_code
            if status in {404, 408, 429} or status >= 500:
                raise MLLMUnavailableError(f"MLLM 暂时不可用，HTTP {status}") from exc
            raise MLLMRequestError(f"MLLM 请求失败，HTTP {status}") from exc

    async def _with_media_references(self, messages, request):
        if not self.settings.use_media_references:
            return await self._send(strip_media_reference_metadata(messages), request)
        coordinator = get_vllm_media_reference_coordinator(
            base_url=self.settings.api_base_url, model=self.settings.model,
        )
        prepared = await coordinator.prepare(messages)
        try:
            result = await self._send(prepared.messages, request)
        except BaseException as error:
            await coordinator.finish(prepared, succeeded=False)
            if not prepared.referenced_media_uuids or not is_vllm_media_cache_miss(error):
                raise
            # 只识别明确的 cache miss；普通 400、连接失败和取消不自动重试。
            await coordinator.invalidate(prepared.media_uuids)
            retry = await coordinator.prepare(messages)
            try:
                result = await self._send(retry.messages, request)
            except BaseException:
                await coordinator.finish(retry, succeeded=False)
                raise
            await coordinator.finish(retry, succeeded=True)
            return result
        await coordinator.finish(prepared, succeeded=True)
        return result

    async def _send(self, messages, request):
        # 缓存填充等待不持有配额；只有实际发送时编码未命中的 PNG 页面。
        async with get_mllm_request_limiter(self.settings.max_concurrent_requests):
            return await self._client.chat.completions.create(
                messages=materialize_image_messages(messages), **request,
            )
