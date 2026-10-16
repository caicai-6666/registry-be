"""单个通行证组的多模态判断：一次请求、严格 JSON 验收与私有审计。"""

import json
import time
from collections.abc import Callable

from pydantic import ValidationError

from app.agent.passport_recommendation.message import build_initial_messages
from app.agent.passport_recommendation.prompt import build_response_format
from app.agent.passport_recommendation.state import PassportGroup, PassportGroupJudgment, PassportRecommendationInput
from app.infrastructure.mllm import MLLMClient
from app.model.pdf import PreparedPDF


class PassportJudgmentError(RuntimeError):
    """未形成有效判断，不能被解释为不推荐或空推荐。"""


def check_request_budget(client: MLLMClient, pdf: PreparedPDF, messages: list[dict], response_format: dict) -> None:
    settings = client.settings
    if any(type(page.visual_tokens) is not int or page.visual_tokens <= 0 for page in pdf.pages):
        raise PassportJudgmentError("当前文件的页面视觉预算无效")
    visual = sum(page.visual_tokens for page in pdf.pages)
    if visual > settings.max_visual_tokens_per_request:
        raise PassportJudgmentError("当前文件超过模型请求视觉预算")
    # 与现有判重预算一致，UTF-8 字节作为文本 token 保守上界，图像不重复计入文本。
    text_bytes = sum(len(block["text"].encode("utf-8")) for message in messages
                     for block in (message["content"] if isinstance(message["content"], list)
                                   else [{"type": "text", "text": message["content"]}]) if block["type"] == "text")
    text_bytes += len(json.dumps(response_format, ensure_ascii=False).encode("utf-8"))
    reserved = settings.generation.max_completion_tokens + settings.reserved_prompt_tokens + settings.reserved_runtime_tokens
    if text_bytes + visual + reserved > settings.context_window_tokens:
        raise PassportJudgmentError("当前文件与通行证组描述超过模型上下文预算")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 不能包含重复字段")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("JSON 不能包含非有限数值")


def parse_judgment(content: str, *, current_page_count: int, group_file_count: int) -> PassportGroupJudgment:
    """只验收完整正文，不提取围栏片段，不从原生思考中猜测结果。"""
    try:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("缺少 JSON 正文")
        value = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return PassportGroupJudgment.model_validate(value, context={
            "current_page_count": current_page_count, "group_file_count": group_file_count,
        })
    except (ValueError, TypeError, ValidationError):
        # 错误文本不回显模型正文；完整原始响应可由私有审计收集。
        raise PassportJudgmentError("模型正文不是符合契约的 JSON 判断，或证据引用无效") from None


async def judge_group(file: PassportRecommendationInput, pdf: PreparedPDF, group: PassportGroup,
                      client: MLLMClient, *, audit_sink: Callable[[list[dict]], None] | None = None) -> PassportGroupJudgment:
    audit = [{"passport": group.passport, "status": "started"}]
    started = time.monotonic()
    try:
        messages = build_initial_messages(file, pdf, group)
        response_format = build_response_format()
        check_request_budget(client, pdf, messages, response_format)
        response = await client.create_chat_completion(messages=messages, response_format=response_format)
        # SDK 原始响应包含 reasoning、用量和正文，独立于最终判断保存。
        audit[0]["response"] = response.model_dump(mode="json")
        if len(response.choices) != 1:
            raise PassportJudgmentError("模型必须返回唯一判断响应")
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.tool_calls or choice.message.refusal:
            raise PassportJudgmentError("模型未正常完成 JSON 判断")
        result = parse_judgment(choice.message.content, current_page_count=pdf.page_count,
                                group_file_count=len(group.files))
        audit[0].update(status="accepted", judgment=result.model_dump())
        return result
    except BaseException as error:
        audit[0].update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        audit[0]["seconds"] = time.monotonic() - started
        if audit_sink is not None:
            audit_sink(audit)
