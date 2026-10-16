"""构建独立 LangGraph 文件比较子图，输入文件对并输出经过校验的结果。"""

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.agent.file_deduplication import node
from app.agent.file_deduplication.context import FileDeduplicationContext
from app.agent.file_deduplication.state import FileDeduplicationInputState, FileDeduplicationOutputState, FileDeduplicationState


def build_file_deduplication_workflow(*, judgment_node: Callable | None = None):
    # 只允许替换实际判断节点，输入输出校验始终保留。
    graph = StateGraph(
        FileDeduplicationState, input_schema=FileDeduplicationInputState,
        output_schema=FileDeduplicationOutputState, context_schema=FileDeduplicationContext,
    )
    graph.add_node("validate_input", node.validate_input)
    graph.add_node("judge_files", judgment_node or node.judge_files)
    graph.add_node("validate_result", node.validate_result)
    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "judge_files")
    graph.add_edge("judge_files", "validate_result")
    graph.add_edge("validate_result", END)
    return graph.compile(name="file_deduplication")
