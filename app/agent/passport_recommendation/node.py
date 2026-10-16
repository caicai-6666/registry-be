"""搜索聚合、组级并发判断及完整结果收束，不伪造未知结论。"""

import asyncio

from langgraph.runtime import Runtime

from app.agent.passport_recommendation.context import PassportRecommendationContext
from app.agent.passport_recommendation.agent import judge_group
from app.agent.passport_recommendation.message import build_current_file_message
from app.infrastructure.mllm import MLLMClient
from app.agent.passport_recommendation.search import PassportGroupSearch
from app.config.passport_recommendation import load_passport_recommendation_settings
from app.agent.passport_recommendation.state import (
    PassportGroup, PassportGroupDecision, PassportGroupJudgment,
    PassportRecommendation, PassportRecommendationInput, PassportRecommendationResult, PassportRecommendationState,
)


def validated_groups(values) -> list[PassportGroup]:
    if type(values) is not list:
        raise ValueError("搜索与聚合必须明确返回通行证组列表")
    groups = [PassportGroup.model_validate(value) for value in values]
    if len({group.passport for group in groups}) != len(groups):
        raise ValueError("同一通行证必须聚合为一个组")
    files_by_id = {}
    for group in groups:
        for file in group.files:
            # 多通行证文件可以跨组出现，但同一资源不能在不同组变成不同的描述或相似度。
            if file.resource_id in files_by_id and files_by_id[file.resource_id] != file:
                raise ValueError("同一正式文件在不同通行证组的信息必须一致")
            files_by_id[file.resource_id] = file
    return groups


async def search_and_aggregate(state: PassportRecommendationState, runtime: Runtime[PassportRecommendationContext]) -> PassportRecommendationState:
    file = PassportRecommendationInput.model_validate(state["file"])
    ctx = runtime.context
    if ctx is None:
        raise RuntimeError("search_and_aggregate：缺少通行证搜索运行依赖")
    search = ctx.group_search
    if search is None:
        if ctx.resource_search is None:
            raise RuntimeError("search_and_aggregate：必须提供正式资源 HNSW 搜索服务")
        search = PassportGroupSearch(
            ctx.resource_search,
            settings=ctx.search_settings or load_passport_recommendation_settings(),
        ).search
    groups = validated_groups(await search(file))
    # 每次请求重置局部结果，不继承此前请求的分组或判断。
    return {"file": file, "groups": groups, "decisions": []}


async def map_reduce_judgments(state: PassportRecommendationState, runtime: Runtime[PassportRecommendationContext]) -> PassportRecommendationState:
    groups = validated_groups(state["groups"])
    if not groups:
        return {"decisions": []}
    ctx = runtime.context
    if ctx is None:
        raise RuntimeError("map_reduce_judgments：缺少运行依赖")
    file = PassportRecommendationInput.model_validate(state["file"])
    pdf = state.get("current_pdf")

    async def run(judgment):
        limit = asyncio.Semaphore(ctx.judgment_concurrency)

        async def judge(group):
            async with limit:
                bounds = {"group_file_count": len(group.files)}
                if pdf is not None:
                    bounds["current_page_count"] = pdf.page_count
                result = PassportGroupJudgment.model_validate(await judgment(file, group), context=bounds)
                # 通行证来自绑定的组，不让模型改写或创建通行证身份。
                return PassportGroupDecision(passport=group.passport, **result.model_dump())

        # 失败时取消并等待其他组，避免请求尚未结束就关闭共享客户端或发布部分结果。
        async with asyncio.TaskGroup() as tasks:
            pending = [tasks.create_task(judge(group)) for group in groups]
        return {"decisions": [task.result() for task in pending], "current_pdf": pdf}

    if ctx.group_judge is not None:
        return await run(ctx.group_judge)
    if pdf is None:
        if ctx.prepare_pdf is None:
            raise RuntimeError("通行证判断必须提供 current_pdf 或 prepare_pdf")
        pdf = await ctx.prepare_pdf()
    # 全组共用完整页面，在创建模型客户端和并发请求之前验证，缺页时不发送。
    build_current_file_message(file, pdf)

    async def run_with_client(client):
        async def judgment(current, group):
            return await judge_group(current, pdf, group, client, audit_sink=ctx.audit_sink)
        return await run(judgment)

    if ctx.mllm is not None:
        return await run_with_client(ctx.mllm)
    # 一个任务只创建一个客户端；注入的客户端由调用者关闭，默认客户端在全部组结束后关闭。
    async with MLLMClient() as client:
        return await run_with_client(client)


def collect_results(state: PassportRecommendationState) -> PassportRecommendationState:
    groups = validated_groups(state["groups"])
    if type(state.get("decisions")) is not list:
        raise ValueError("并发判断必须明确返回完整结果列表")
    decisions = [PassportGroupDecision.model_validate(value) for value in state["decisions"]]
    by_passport = {item.passport: item for item in decisions}
    if len(by_passport) != len(decisions) or set(by_passport) != {group.passport for group in groups}:
        raise ValueError("每个通行证组必须恰好有一个判断，不能遗漏、重复或混入其他组")
    recommendations = []
    pdf = state.get("current_pdf")
    for group in groups:
        bounds = {"group_file_count": len(group.files)}
        if pdf is not None:
            bounds["current_page_count"] = pdf.page_count
        # 最终出口按实际绑定组验收；全部判断有效后才返回，不能跳过无效的不推荐判断。
        decision = PassportGroupDecision.model_validate(by_passport[group.passport], context=bounds)
        if decision.should_recommend:
            # 新建对外条目，仅保留已有通行证及审核者阅读的最终理由，排序沿用组顺序。
            recommendations.append(PassportRecommendation(passport=group.passport, reason=decision.reason))
    result = PassportRecommendationResult(recommendations=recommendations)
    return {"recommendations": result.recommendations}
