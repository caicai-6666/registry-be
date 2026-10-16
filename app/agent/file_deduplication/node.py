"""判重子图的输入校验、对话运行及终态校验。"""

from langgraph.runtime import Runtime

from app.agent.file_deduplication.context import FileDeduplicationContext
from app.agent.file_deduplication.agent import compare_files
from app.infrastructure.mllm import MLLMClient
from app.agent.file_deduplication.state import FileDeduplicationInput, FileDeduplicationResult, FileDeduplicationState


def validate_input(state: FileDeduplicationState) -> FileDeduplicationState:
    return {"pair": FileDeduplicationInput.model_validate(state["pair"])}


async def judge_files(state: FileDeduplicationState, runtime: Runtime[FileDeduplicationContext]) -> FileDeduplicationState:
    context = runtime.context or FileDeduplicationContext()
    if context.mllm is not None:
        return {"result": await compare_files(state["pair"], context)}
    # 注入客户端由创建者关闭；默认路径独立创建并在成功、失败或取消时释放。
    async with MLLMClient() as client:
        context = FileDeduplicationContext(client, context.settings, context.audit_sink)
        return {"result": await compare_files(state["pair"], context)}


def validate_result(state: FileDeduplicationState) -> FileDeduplicationState:
    if "result" not in state:
        raise ValueError("判重智能体必须返回 result")
    return {"result": FileDeduplicationResult.model_validate(state["result"])}
