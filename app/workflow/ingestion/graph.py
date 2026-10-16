"""组装入库节点与条件分支；不在这里实现外部系统操作。"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from app.workflow.ingestion.context import IngestionWorkflowContext

from app.model.resource import IngestionDelivery
from app.workflow.ingestion import nodes
from app.workflow.ingestion.state import IngestionWorkflowState

WorkflowNode = Callable[..., IngestionWorkflowState | Awaitable[IngestionWorkflowState]]



def dispatch_comparisons(state: IngestionWorkflowState) -> list[Send] | Literal["reduce_comparisons"]:
    candidates = state["duplicate_candidates"]
    if not candidates:
        return "reduce_comparisons"
    return [Send("compare_files", {"delivery": state["delivery"], "current_pdf": state["current_pdf"], "comparison_candidate": candidate})
            for candidate in candidates]


def route_after_candidates(state: IngestionWorkflowState) -> Literal["prepare_comparison_file", "reduce_comparisons"]:
    return "prepare_comparison_file" if state["duplicate_candidates"] else "reduce_comparisons"

def route_after_pull(state: IngestionWorkflowState) -> Literal["check_source_identity", "end"]:
    if "delivery" not in state:
        raise ValueError("拉取节点必须明确返回 delivery")
    if state["delivery"] is None:
        return "end"
    if not isinstance(state["delivery"], IngestionDelivery):
        raise ValueError("拉取节点必须返回已校验的申请")
    return "check_source_identity"


def route_after_identity(state):
    if state.get("pending_review_saved") is True:
        return "acknowledge_request"
    return "publish_rejection" if state.get("is_duplicate") is True else "download_file"


def route_after_download(state):
    return "restore_committed_file" if state.get("committed_review") is not None else "embed_abstract"


def route_after_comparison(state: IngestionWorkflowState) -> Literal["publish_rejection", "recommend_passports"]:
    # 未实现或无效的判断不能被默认当成不重复，从而继续入库。
    if type(state.get("is_duplicate")) is not bool:
        raise ValueError("文件对比节点必须明确返回是否重复")
    return "publish_rejection" if state["is_duplicate"] else "recommend_passports"


def route_after_rejection(state: IngestionWorkflowState) -> Literal["acknowledge_request"]:
    if state.get("rejection_published") is not True:
        raise ValueError("拒绝结果未确认发送成功，不能 ACK")
    return "acknowledge_request"


def route_after_persistence(state: IngestionWorkflowState) -> Literal["acknowledge_request", "publish_rejection"]:
    if state.get("is_duplicate") is True and state.get("pending_review_saved") is not True:
        return "publish_rejection"
    if state.get("pending_review_saved") is not True:
        raise ValueError("文件及待审核记录未确认保存成功，不能 ACK")
    return "acknowledge_request"


def build_ingestion_workflow(
    node_overrides: Mapping[str, WorkflowNode] | None = None,
    *, interrupt_before_comparison: bool = False,
) -> CompiledStateGraph:
    handlers: dict[str, WorkflowNode] = {
        "pull_request": nodes.pull_request,
        "check_source_identity": nodes.check_source_identity,
        "restore_committed_file": nodes.restore_committed_file,
        "download_file": nodes.download_file,
        "embed_abstract": nodes.embed_abstract,
        "find_duplicate_candidates": nodes.find_duplicate_candidates,
        "prepare_comparison_file": nodes.prepare_comparison_file,
        "compare_files": nodes.compare_files,
        "reduce_comparisons": nodes.reduce_comparisons,
        "publish_rejection": nodes.publish_rejection,
        "recommend_passports": nodes.recommend_passports,
        "persist_pending_review": nodes.persist_pending_review,
        "acknowledge_request": nodes.acknowledge_request,
    }
    if node_overrides:
        if set(node_overrides) - handlers.keys():
            raise ValueError("存在未知入库工作流节点")
        handlers.update(node_overrides)
    graph = StateGraph(IngestionWorkflowState, context_schema=IngestionWorkflowContext)
    for name, handler in handlers.items():
        graph.add_node(name, handler)
    graph.add_edge(START, "pull_request")
    graph.add_conditional_edges("pull_request", route_after_pull, {"check_source_identity": "check_source_identity", "end": END})
    graph.add_conditional_edges("check_source_identity", route_after_identity, ["acknowledge_request","publish_rejection","download_file"])
    graph.add_conditional_edges("download_file", route_after_download, ["restore_committed_file","embed_abstract"])
    graph.add_edge("restore_committed_file", "acknowledge_request")
    graph.add_edge("embed_abstract", "find_duplicate_candidates")
    graph.add_conditional_edges("find_duplicate_candidates", route_after_candidates)
    graph.add_conditional_edges("prepare_comparison_file", dispatch_comparisons, ["compare_files", "reduce_comparisons"])
    graph.add_edge("compare_files", "reduce_comparisons")
    graph.add_conditional_edges("reduce_comparisons", route_after_comparison, {
        "publish_rejection": "publish_rejection", "recommend_passports": "recommend_passports",
    })
    graph.add_edge("recommend_passports", "persist_pending_review")
    graph.add_conditional_edges("publish_rejection", route_after_rejection, {"acknowledge_request": "acknowledge_request"})
    graph.add_conditional_edges("persist_pending_review", route_after_persistence, {"acknowledge_request": "acknowledge_request", "publish_rejection": "publish_rejection"})
    graph.add_edge("acknowledge_request", END)
    return graph.compile(name="ingestion", interrupt_before=["prepare_comparison_file", "compare_files", "reduce_comparisons", "restore_committed_file", "publish_rejection", "acknowledge_request"] if interrupt_before_comparison else None)
