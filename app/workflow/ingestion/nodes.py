"""入库前置处理、并行判重子图调用、推荐适配与拒绝反馈。"""

import asyncio
from dataclasses import replace

from langgraph.runtime import Runtime
from langgraph.types import Overwrite
from app.agent.file_deduplication import ComparisonFile, FileDeduplicationInput, FileDeduplicationResult
from app.agent.passport_recommendation import PassportRecommendationInput, PassportRecommendationResult
from app.service.ingestion_rejection import build_duplicate_rejection_note
from app.service.source_identity import active_source_reason
from app.model.resource import ActiveSourceConflict, StoredFile
from app.model.resource import IngestionDelivery
from app.infrastructure.resource_store import ResourceFileStore

from app.workflow.ingestion.context import IngestionWorkflowContext
from app.workflow.ingestion.state import ComparisonResult, IngestionWorkflowState


def context(runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowContext:
    if not isinstance(runtime.context, IngestionWorkflowContext):
        raise NotImplementedError("请提供入库工作流运行依赖 context")
    return runtime.context


async def pull_request(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    token = ctx.token()
    try:
        async with asyncio.timeout(ctx.request_timeout_seconds):
            delivery = await ctx.middleware.pull_ingestion(token)
    except Exception as error:
        ctx.invalidate_on_unauthorized(error, token)
        raise
    return initial_delivery_state(delivery)


def initial_delivery_state(delivery: IngestionDelivery | None) -> IngestionWorkflowState:
    """新申请与失败重试均从干净状态开始，已提交记录由 SQLite 恢复。"""
    return {"delivery": delivery, "committed_review": None, "file_content": b"", "current_pdf": None, "abstract_embedding": [],
            "duplicate_candidates": [], "comparison_results": Overwrite([]),
            "is_duplicate": None, "duplicate_source_id": None, "duplicate_reason": "", "rejection_published": False,
            "pending_review_saved": False, "acknowledged": False, "passport_recommendations": None}


def source_conflict_update(resource):
    return {"is_duplicate": True, "duplicate_source_id": resource.source_id,
            "duplicate_reason": active_source_reason(resource), "pending_review_saved": False}


async def check_source_identity(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    if ctx.resource_repository is None:
        raise ValueError("请在入库 context 中提供资源仓库以检查源文件身份")
    message = state["delivery"].message
    if ctx.reviews is None:
        raise ValueError("请在入库 context 中提供审核仓库 reviews")
    review = await ctx.reviews.find(state["delivery"])
    if review is not None and review.get("decision") is not None:
        # 人工决定已持久化，后续由审核服务恢复；消息重投不能再判重、写文件或自动反馈。
        return {"pending_review_saved": True, "review_id": review["id"]}
    if review is not None and review["status"] == "rejected":
        # 已确定的拒绝结论复用 SQLite 记录，无须重新运行智能体。
        return {"is_duplicate": True}
    if review is not None:
        root = ctx.settings.resource_root if review["status"] == "approved" else ctx.settings.ingestion_file_root
        store = ResourceFileStore(root)
        deleted = False
        if review["status"] == "approved" and review["resource_id"]:
            existing = await ctx.resource_repository.find(message.platform_code,message.file_url,resource_id=int(review["resource_id"]))
            deleted = existing is None
        if deleted or await store.exists(message.platform_code,StoredFile(message.file_url)):
            return {"pending_review_saved": True, "review_id": review["id"]}
        return {"committed_review": review}
    pending = await ctx.reviews.find_pending_source(message.platform_code,message.source_id,exclude_message_id=message.message_id)
    if pending is not None:
        return source_conflict_update(ctx.reviews.pending_resource(pending))
    active = await ctx.resource_repository.find_active_source(
        message.platform_code,message.source_id,exclude_file_url=message.file_url,
    )
    return source_conflict_update(active) if active is not None else {}



async def restore_committed_file(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    review = state["committed_review"]
    message = state["delivery"].message
    root = ctx.settings.resource_root if review["status"] == "approved" else ctx.settings.ingestion_file_root
    store = ResourceFileStore(root)
    async def chunks():
        yield state["file_content"]
    try:
        await store.save(state["delivery"].message,chunks(),expected_size=len(state["file_content"]),overwrite=False)
    except FileExistsError:
        pass
    if await store.read(message.platform_code,message.file_url,max_file_bytes=ctx.settings.max_file_bytes) != state["file_content"]:
        raise ValueError("补回附件与当前申请内容不一致")
    return {"pending_review_saved": True, "review_id": review["id"]}


async def download_file(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    token = ctx.token()
    content = bytearray()
    try:
        async with asyncio.timeout(ctx.settings.download_timeout_seconds):
            async with ctx.middleware.open_ingestion_file(state["delivery"].message.file, token) as response:
                length = response.headers.get("content-length")
                expected = int(length) if length is not None else None
                if expected is not None and (expected < 0 or expected > ctx.settings.max_file_bytes):
                    raise ValueError("附件长度超出内存下载限制")
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    if len(content) + len(chunk) > ctx.settings.max_file_bytes:
                        raise ValueError("附件长度超出内存下载限制")
                    content.extend(chunk)
                if expected is not None and len(content) != expected:
                    raise ValueError("下载文件长度与响应不一致")
    except Exception as error:
        ctx.invalidate_on_unauthorized(error, token)
        raise
    return {"file_content": bytes(content)}


async def embed_abstract(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    abstract = state["delivery"].message.abstract
    if not abstract or not abstract.strip():
        raise ValueError("入库摘要不能为空，不能跳过摘要去重")
    return {"abstract_embedding": await ctx.embeddings.embed_text(abstract)}


async def find_duplicate_candidates(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    candidates = await ctx.candidates.search(
        state["delivery"].message.platform_code, state["abstract_embedding"],
        top_k=ctx.settings.duplicate_top_k, min_similarity=ctx.settings.duplicate_min_similarity,
    )
    return {"duplicate_candidates": candidates}


async def prepare_comparison_file(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    message = state["delivery"].message
    prepared = await ctx.pdf_service.prepare(
        state["file_content"], platform_code=message.platform_code, file_name=message.file.filename,
    )
    # 在 fan-out 前只渲染一次，所有比较分支复用同一份页面对象和媒体 UUID。
    return {"current_pdf": prepared}


async def compare_files(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    candidate = state["comparison_candidate"]
    try:
        async with ctx.comparison_limit:
            similar_pdf = await ctx.pdf_service.load(candidate["source_platform"], candidate["file_url"])
            message = state["delivery"].message
            pair = FileDeduplicationInput(
                current_file=ComparisonFile(name=message.name, abstract=message.abstract, pdf=state["current_pdf"]),
                similar_file=ComparisonFile(name=candidate["name"], abstract=candidate["abstract"], pdf=similar_pdf,
                                            applicant=candidate.get("applicant"), reviewer=candidate.get("reviewer")),
            )
            output = await ctx.deduplication_workflow.ainvoke({"pair": pair}, context=ctx.deduplication_context)
            result = FileDeduplicationResult.model_validate(output["result"])
        outcome = ComparisonResult(candidate["resource_id"], result.is_duplicate, reason=result.reason)
    except NotImplementedError:
        raise
    except Exception as error:
        # 未知结果不能当作不重复；不把错误原文或智能体失败对话放入业务状态。
        outcome = ComparisonResult(candidate["resource_id"], None, type(error).__name__)
    return {"comparison_results": [outcome]}


def reduce_comparisons(state: IngestionWorkflowState) -> IngestionWorkflowState:
    candidates = state["duplicate_candidates"]
    results = state.get("comparison_results", [])
    expected = {candidate["resource_id"] for candidate in candidates}
    actual = {result.resource_id for result in results}
    if len(expected) != len(candidates) or len(results) != len(actual) or expected != actual:
        raise ValueError("文件对比结果不完整或重复，不能推进审核")
    by_id = {result.resource_id: result for result in results}
    duplicates = [candidate for candidate in candidates if by_id[candidate["resource_id"]].is_duplicate is True]
    if duplicates:
        identified = next((candidate for candidate in duplicates if candidate["source_id"] and candidate["source_id"].strip()), None)
        if identified is None:
            raise ValueError("命中的重复文件缺少 source_id，不能构造拒绝反馈")
        source_id = identified["source_id"]
        return {"is_duplicate": True, "duplicate_source_id": source_id,
                "duplicate_reason": by_id[identified["resource_id"]].reason}
    if any(result.is_duplicate is not False or result.error_type for result in results):
        raise RuntimeError("部分文件对比失败，不能判断为不重复")
    return {"is_duplicate": False}


async def publish_rejection(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    if state.get("is_duplicate") is not True:
        raise ValueError("未确认重复，不能自动拒绝")
    if ctx.reviews is None:
        raise ValueError("请在入库 context 中提供审核仓库 reviews")
    review = await ctx.reviews.find(state["delivery"])
    note = (review["reviewer_note"] if review is not None and review["status"] == "rejected"
            else build_duplicate_rejection_note(state["duplicate_source_id"], state["duplicate_reason"]))
    await ctx.reviews.save_rejected(state["delivery"], note)
    review = await ctx.reviews.find(state["delivery"])
    if review["result_message_id"] is not None:
        return {"rejection_published": True, "rejection_message_id": review["result_message_id"]}
    token = ctx.token()
    try:
        async with asyncio.timeout(ctx.request_timeout_seconds):
            message_id = await ctx.middleware.publish_ingestion_rejection(
                state["delivery"], token, note=note,
            )
        await ctx.reviews.mark_rejection_published(state["delivery"], message_id)
    except Exception as error:
        # 包含 409：当前接口无法证明旧反馈内容相同，不能把它直接当作成功。
        ctx.invalidate_on_unauthorized(error, token)
        raise
    return {"rejection_published": True, "rejection_message_id": message_id}


async def recommend_passports(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    if state.get("is_duplicate") is not False:
        raise ValueError("仅明确不重复的文件可以进入通行证推荐")
    message = state["delivery"].message
    file = PassportRecommendationInput(
        name=message.name, abstract=message.abstract, abstract_embedding=state["abstract_embedding"],
    )
    current_pdf = state.get("current_pdf")

    async def prepare_current_pdf():
        nonlocal current_pdf
        # 推荐组为空时不渲染；没有判重候选时，节点 2 在并发前仅调用一次。
        if current_pdf is None:
            current_pdf = await ctx.pdf_service.prepare(
                state["file_content"], platform_code=message.platform_code, file_name=message.file.filename,
            )
        return current_pdf

    passport_context = replace(ctx.passport_context, prepare_pdf=prepare_current_pdf)
    output = await ctx.passport_workflow.ainvoke({"file": file, "current_pdf": current_pdf}, context=passport_context)
    result = PassportRecommendationResult.model_validate(output)
    return {"passport_recommendations": result.recommendations, "current_pdf": current_pdf}


async def persist_pending_review(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    if state.get("is_duplicate") is not False or state.get("passport_recommendations") is None:
        raise ValueError("确认不重复并完成推荐后才能保存待审核文件")
    if ctx.pending_review_service is None:
        raise ValueError("请在入库 context 中提供 pending_review_service")
    if ctx.pending_review_service.files.root != ctx.settings.ingestion_file_root:
        raise ValueError("待审核原文件必须写入 ingestion 暂存目录")
    try:
        review_id = await ctx.pending_review_service.persist(
            state["delivery"], state["file_content"], embedding=state["abstract_embedding"],
            recommendations=state["passport_recommendations"],
        )
    except ActiveSourceConflict as error:
        return source_conflict_update(error.resource)
    if type(review_id) is not int or review_id <= 0:
        raise ValueError("SQLite 必须返回有效审核记录 ID，才能确认保存成功")
    return {"pending_review_saved": True, "review_id": review_id}


async def acknowledge_request(state: IngestionWorkflowState, runtime: Runtime[IngestionWorkflowContext]) -> IngestionWorkflowState:
    ctx = context(runtime)
    if state.get("rejection_published") is not True and state.get("pending_review_saved") is not True:
        raise ValueError("拒绝结果未发送或资源未保存，不能 ACK")
    if ctx.reviews is None:
        raise ValueError("审核仓库未提供，不能 ACK")
    if state.get("pending_review_saved") is True:
        review = await ctx.reviews.find(state["delivery"])
        # 原申请已完整暂存后可以被人工拒绝；该决定的反馈由审核恢复器负责，不能走自动拒绝。
        human_rejected = review is not None and review["status"] == "rejected" and review.get("decision") == "rejected"
        if review is None or review["id"] != state["review_id"] or (review["status"] not in ("pending","approved") and not human_rejected):
            raise ValueError("待审核申请尚未保存在 SQLite，不能 ACK")
    else:
        review = await ctx.reviews.find(state["delivery"])
        if review is None or review["status"] != "rejected" or review["result_message_id"] is None:
            raise ValueError("拒绝审核及发布凭据未保存，不能 ACK")
    token = ctx.token()
    try:
        async with asyncio.timeout(ctx.request_timeout_seconds):
            await ctx.middleware.acknowledge_ingestion(state["delivery"], token)
    except Exception as error:
        ctx.invalidate_on_unauthorized(error, token)
        raise
    return {"acknowledged": True}
