"""固定三个功能节点，输入与输出校验分别嵌入首尾节点。"""

from langgraph.graph import END, START, StateGraph

from app.agent.passport_recommendation import node
from app.agent.passport_recommendation.context import PassportRecommendationContext
from app.agent.passport_recommendation.state import PassportRecommendationInputState, PassportRecommendationOutputState, PassportRecommendationState


def build_passport_recommendation_workflow():
    graph = StateGraph(
        PassportRecommendationState, input_schema=PassportRecommendationInputState,
        output_schema=PassportRecommendationOutputState, context_schema=PassportRecommendationContext,
    )
    graph.add_node("search_and_aggregate", node.search_and_aggregate)
    graph.add_node("map_reduce_judgments", node.map_reduce_judgments)
    graph.add_node("collect_results", node.collect_results)
    graph.add_edge(START, "search_and_aggregate")
    graph.add_edge("search_and_aggregate", "map_reduce_judgments")
    graph.add_edge("map_reduce_judgments", "collect_results")
    graph.add_edge("collect_results", END)
    return graph.compile(name="passport_recommendation")
