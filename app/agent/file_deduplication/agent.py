"""文件 A/B 对话循环：有限纠错、友好折叠与终态验收。"""

import json
import time

from pydantic import ValidationError

from app.agent.file_deduplication.context import FileDeduplicationContext
from app.agent.file_deduplication.message import build_initial_messages, build_file_b_pages_content, fold_file_b_pages
from app.agent.file_deduplication.state import FileDeduplicationInput
from app.agent.file_deduplication.tool import (
    build_file_deduplication_tools, comparison_result, finish_comparison, read_candidate_pages,
)
from app.config.file_deduplication import load_file_deduplication_settings


class FileComparisonError(RuntimeError):
    """没有形成已校验终态；不能据此返回 false。"""


def check_request_budget(client, pair, visible_pages, messages, tools):
    """以 UTF-8 字节作为文字 token 保守上界，避免把长观察无限累积。"""
    settings = client.settings
    pages = list(pair.current_file.pdf.pages) + [pair.similar_file.pdf.pages[p - 1] for p in visible_pages]
    if any(type(page.visual_tokens) is not int or page.visual_tokens <= 0 for page in pages):
        raise FileComparisonError("页面视觉预算无效")
    visual = sum(page.visual_tokens for page in pages)
    if visual > settings.max_visual_tokens_per_request:
        raise FileComparisonError("页面窗口超过视觉预算")
    # 图像字节不计入文字；实际视觉开销使用渲染器给出的估算。
    text = []
    for message in messages:
        content = message.get("content") or ""
        text.append(content if isinstance(content, str) else "".join(
            block.get("text", "") for block in content if block.get("type") == "text"))
        text.append(json.dumps(message.get("tool_calls", []), ensure_ascii=False))
        # 原生思考独立于正文，连续保留时同样占用上下文预算。
        text.append(message.get("reasoning_content") or "")
    text.append(json.dumps(tools, ensure_ascii=False))
    estimated = sum(len(part.encode("utf-8")) for part in text) + visual
    reserved = settings.generation.max_completion_tokens + settings.reserved_prompt_tokens + settings.reserved_runtime_tokens
    if estimated + reserved > settings.context_window_tokens:
        raise FileComparisonError("比较内容超过上下文预算")


def _schema_feedback(error: ValidationError) -> str:
    # 不回显模型提交的无效字段值；只给出位置、错误类型与修正方向。
    errors = error.errors(include_input=False, include_url=False)
    return "；".join(f"{'.'.join(map(str, item['loc'])) or 'arguments'}：{item['type']}，请按工具字段约束修正"
                     for item in errors[:3])


def _tool_receipt(call, parsed, feedback, message):
    assistant = {"role": "assistant", "content": None, "tool_calls": [{"id": call.id,
             "type": "function", "function": {"name": call.function.name, "arguments": parsed.model_dump_json()}}]}
    # 不同版本的 vLLM 接口和 Qwen 模板读取不同别名，同时发送同一原文以兼容二者。
    # 不拼进普通正文、不截断原文；完整原始响应及字段名仍保存在私有审计。
    for field in ("reasoning_content", "reasoning"):
        value = getattr(message, field, None)
        if isinstance(value, str) and value:
            assistant["reasoning_content"] = value
            assistant["reasoning"] = value
            break
    return [assistant, {"role": "tool", "tool_call_id": call.id, "content": feedback}]


async def compare_files(pair: FileDeduplicationInput, context: FileDeduplicationContext):
    """独立会话运行到完成工具被接受，否则明确失败。"""
    if context.mllm is None:
        raise FileComparisonError("文件比较需要模型客户端")
    settings = context.settings or load_file_deduplication_settings()
    tools = build_file_deduplication_tools(max_visible_pages=settings.max_visible_pages)
    prefix, task_index = build_initial_messages(pair)
    # 仅保留工具执行所需的局部页面元信息，判断过程由真实对话和原生思考延续。
    visible_pages, presented_pages, observations, reads = [], set(), [], 0
    corrections, history, audit = [], [], []
    consecutive_errors = 0
    try:
        for round_number in range(1, settings.max_rounds + 1):
            messages = prefix + history + corrections
            check_request_budget(context.mllm, pair, visible_pages, messages, tools)
            started = time.monotonic()
            try:
                response = await context.mllm.create_chat_completion(messages=messages, tools=tools,
                    tool_choice="auto", tool_placement="before_task", tool_task_index=task_index,
                    preserve_thinking=True)
            except BaseException as error:
                audit.append({"round": round_number, "status": "request_failed", "error": type(error).__name__,
                              "seconds": time.monotonic() - started})
                raise
            # 已展示意味着请求确实成功送达并得到响应，不意味着模型已经充分检查。
            presented_pages.update(visible_pages)
            entry = {"round": round_number, "seconds": time.monotonic() - started,
                     "response": response.model_dump(mode="json"), "status": "received"}
            audit.append(entry)
            try:
                if len(response.choices) != 1:
                    raise ValueError("需要唯一响应；请只调用一个提供的真实函数工具")
                choice = response.choices[0]
                calls = choice.message.tool_calls or []
                if choice.finish_reason != "tool_calls" or len(calls) != 1:
                    raise ValueError(f"只解析到 {len(calls)} 个工具调用或响应未完整结束；"
                        "本轮必须且只能调用一个提供的真实函数工具，不用普通文本模拟")
                call = calls[0]
                if call.type != "function" or not call.id or len(call.id) > 200:
                    raise ValueError("工具标识无效；请使用提供的真实函数工具")
                name = call.function.name
                if name not in {"read_candidate_pages", "finish_comparison"}:
                    raise ValueError("未知工具；请选择 read_candidate_pages 或 finish_comparison")
                arguments = json.loads(call.function.arguments)
                if name == "read_candidate_pages":
                    parsed = read_candidate_pages(arguments, current_page_count=pair.current_file.pdf.page_count,
                        candidate_page_count=pair.similar_file.pdf.page_count, max_visible_pages=settings.max_visible_pages,
                        visible_candidate_pages=set(visible_pages) & presented_pages)
                    if reads >= settings.max_page_reads:
                        raise ValueError("读页次数已达上限；证据充分时提交结果，否则保持未完成")
                    next_observations = list(observations)
                    for observation in parsed.observations:
                        if observation not in next_observations:
                            next_observations.append(observation)
                    if len(next_observations) > settings.max_observations:
                        raise ValueError("观察记录已达上限；精简本次记录，不重复保存相同事实")
                    next_visible = [page for page in visible_pages if page not in parsed.page_numbers]
                    next_visible = (next_visible + parsed.page_numbers)[-settings.max_visible_pages:]
                    feedback = build_file_b_pages_content(pair.similar_file.pdf, parsed.page_numbers)
                    next_receipt = _tool_receipt(call, parsed, feedback, choice.message)
                    next_history = fold_file_b_pages(history + next_receipt,
                        visible_pages=next_visible, observations=next_observations)
                    check_request_budget(context.mllm, pair, next_visible, prefix + next_history, tools)
                else:
                    parsed = finish_comparison(arguments, current_page_count=pair.current_file.pdf.page_count,
                        candidate_page_count=pair.similar_file.pdf.page_count, presented_candidate_pages=presented_pages)
                    result = comparison_result(parsed)
                    entry.update(status="accepted", tool=name, arguments=parsed.model_dump())
                    return result
            except FileComparisonError as error:
                entry.update(status="failed", error=str(error))
                raise
            except (ValueError, TypeError) as error:
                feedback = _schema_feedback(error) if isinstance(error, ValidationError) else (
                    "arguments：JSON 无效，请提交合法工具参数" if isinstance(error, json.JSONDecodeError) else str(error))
                # 连续失败只保留最小反馈；原始普通正文和无效参数仅在私有审计。
                entry.update(status="rejected", feedback=feedback)
                # 无合法调用时不能伪造 tool_call_id；以程序生成的 assistant 校验提示纠错。
                # 不追加 user，不回填模型普通正文、原生思考或完整无效参数。
                corrections.append({"role": "assistant", "content": "程序校验反馈：上次动作未被接受：" + feedback})
                consecutive_errors += 1
                if consecutive_errors >= settings.max_consecutive_errors:
                    raise FileComparisonError("连续纠错达到上限，文件比较未完成") from error
                continue
            # 原子接受后清除整段纠错链；合法动作及其完整原生思考按轮次持续保留。
            corrections.clear()
            consecutive_errors = 0
            entry.update(status="accepted", tool=name, arguments=parsed.model_dump())
            history, visible_pages, observations = next_history, next_visible, next_observations
            reads += 1
        raise FileComparisonError("对话轮次达到上限，文件比较未完成")
    finally:
        # 审计由调用方选择收集，默认仅存在内存，不向真实数据目录写测试或对话产物。
        if context.audit_sink is not None:
            context.audit_sink(audit)
