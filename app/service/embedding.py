"""使用 LlamaIndex 异步调用 vLLM embedding，供索引与检索复用。"""

import asyncio
import math

import httpx
from llama_index.embeddings.openai_like import OpenAILikeEmbedding
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from app.config.embedding import EmbeddingSettings


SUMMARY_EMBEDDING_INSTRUCTION = "检索与这份摘要语义相关的文本。"


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    data: list[_EmbeddingItem]


def validate_vector(vector: list[float], dimensions: int) -> None:
    if len(vector) != dimensions or any(not math.isfinite(value) for value in vector):
        raise ValueError("向量维度不符或含非有限数值")
    if not any(vector):
        raise ValueError("余弦匹配不接受零向量")


class VLLMEmbedding(OpenAILikeEmbedding):
    expected_dimensions: int = Field(ge=1, le=4096)
    normalize: bool = True
    _request_limit: asyncio.Semaphore = PrivateAttr()
    _embedding_http: httpx.AsyncClient = PrivateAttr()

    def __init__(self, *, max_concurrent_requests: int, async_http_client: httpx.AsyncClient, **kwargs):
        super().__init__(async_http_client=async_http_client, **kwargs)
        self._embedding_http = async_http_client
        # 所有单条、批量与查询请求共用一个限制，不依赖批量调度器的并发行为。
        self._request_limit = asyncio.Semaphore(max_concurrent_requests)

    def _prepare_vector(self, vector: list[float]) -> list[float]:
        validate_vector(vector, self.expected_dimensions)
        if self.normalize:
            norm = math.hypot(*vector)
            return [value / norm for value in vector]
        return vector

    async def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("待生成向量的文本不能为空")
        # system 是统一的相似检索任务，user 仅放摘要；不把聊天模板拼进普通 input。
        payload = {
            "model": self.model_name, "encoding_format": "float",
            "messages": [[{"role": "system", "content": SUMMARY_EMBEDDING_INSTRUCTION},
                          {"role": "user", "content": text}] for text in texts],
            "add_generation_prompt": True, "add_special_tokens": False,
        }
        async with self._request_limit:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await self._embedding_http.post(
                        str(self.api_base).rstrip("/") + "/embeddings", json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ValueError("embedding 成功响应必须返回 200")
                    result = _EmbeddingResponse.model_validate(response.json())
                    rows = sorted(result.data, key=lambda row: row.index)
                    if [row.index for row in rows] != list(range(len(texts))):
                        raise ValueError("embedding 返回数量或索引与输入不一致")
                    return [self._prepare_vector(row.embedding) for row in rows]
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as error:
                    retryable = not isinstance(error, httpx.HTTPStatusError) or error.response.status_code in (429, 500, 502, 503, 504)
                    if not retryable or attempt == self.max_retries:
                        raise
                    # 仅重试临时网络/服务故障，不重试凭据、输入或响应契约错误。
                    await asyncio.sleep(min(2 ** attempt, 4))
        raise RuntimeError("embedding 请求未完成")

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return (await self._request_embeddings([text]))[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return (await self._request_embeddings([query]))[0]

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return await self._request_embeddings(texts)

    def _get_text_embedding(self, text):
        raise NotImplementedError("vLLM 服务请使用异步 embedding 方法")

    def _get_query_embedding(self, query):
        raise NotImplementedError("vLLM 服务请使用异步 embedding 方法")

    def _get_text_embeddings(self, texts):
        raise NotImplementedError("vLLM 服务请使用异步 embedding 方法")


class EmbeddingService:
    def __init__(self, settings: EmbeddingSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._http = httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False, transport=transport)
        base = str(settings.base_url).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        # 任意 vLLM 模型名使用 OpenAILikeEmbedding，不套用 OpenAI 模型名枚举。
        self.model = VLLMEmbedding(
            model_name=settings.model, api_base=base,
            api_key=settings.api_key.get_secret_value() or "unused",
            timeout=settings.timeout_seconds, max_retries=2,
            async_http_client=self._http,
            additional_kwargs={"encoding_format": "float"},
            embed_batch_size=settings.batch_size,
            num_workers=settings.max_concurrent_requests,
            max_concurrent_requests=settings.max_concurrent_requests,
            expected_dimensions=settings.dimensions, normalize=settings.normalize,
        )

    async def embed_text(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("待生成向量的文本不能为空")
        vector = await self.model.aget_text_embedding(text)
        validate_vector(vector, self.settings.dimensions)
        return vector

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("待生成向量的文本不能为空")
        return await self.model.aget_text_embedding_batch(texts)

    async def close(self) -> None:
        await self._http.aclose()
