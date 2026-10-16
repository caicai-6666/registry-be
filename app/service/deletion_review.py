"""人工删除审核：原件保留、正式资源移除、审核结论与反馈恢复。"""

import asyncio
import logging
from weakref import WeakValueDictionary

import httpx

from app.infrastructure.middleware import MiddlewareClient
from app.model.resource import StoredFile
from app.model.passport import same_passports
from app.model.review import ReviewNotFound, ReviewConflict, ReviewUnavailable
from app.repository.deletion_review import SQLiteDeletionReviewRepository
from app.repository.resource import MySQLResourceRepository, ResourceDeletionConflict
from app.schema.deletion_review import DeletionReviewDecision, DeletionReviewResultResponse
from app.service.middleware_session import MiddlewareSessionService
from app.service.review_files import ReviewFileService

logger = logging.getLogger(__name__)


class DeletionReviewService:
    def __init__(self, reviews: SQLiteDeletionReviewRepository, resources: MySQLResourceRepository,
                 files: ReviewFileService, sessions: MiddlewareSessionService, middleware: MiddlewareClient | None,
                 *, timeout_seconds: float = 40, retry_interval_seconds: float = 30):
        self.reviews, self.resources, self.files = reviews, resources, files
        self.sessions, self.middleware = sessions, middleware
        self.timeout_seconds, self.retry_interval_seconds = timeout_seconds, retry_interval_seconds
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
        self._task: asyncio.Task | None = None

    def _lock(self, review_id: int) -> asyncio.Lock:
        lock = self._locks.get(review_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[review_id] = lock
        return lock

    def _token(self):
        token = self.sessions.get_access_token()
        if self.middleware is None or token is None:
            raise ReviewUnavailable("中间件会话尚未就绪，请稍后重试")
        return token

    async def _review(self, review_id: int) -> dict:
        row = await self.reviews.get_by_id(review_id)
        if row is None:
            raise ReviewNotFound("删除审核申请不存在")
        return row

    async def decide(self, review_id: int, body: DeletionReviewDecision, reviewer: str) -> DeletionReviewResultResponse:
        async with self._lock(review_id):
            row = await self._review(review_id)
            if row["decision"] is None:
                if row["status"] != "pending":
                    raise ReviewConflict("该删除申请已经完成审核")
                if row["resource_id"] is None:
                    raise ReviewConflict("删除申请缺少正式资源关联")
                self._token()
                if body.approved:
                    if row["file_url"] is None or row["source_id"] is None:
                        raise ReviewConflict("删除申请缺少正式文件身份信息")
                    target = await self.resources.find_deletion_source(row["source_platform"], row["source_id"])
                    if (target is None
                            or (str(target.resource_id), target.file_url) != (row["resource_id"], row["file_url"])
                            or not same_passports(target.passport, row["passport"])):
                        raise ReviewConflict("待删除正式资源不存在或引用已失效")
                    await self.files.read_resource(row["source_platform"], row["file_url"])
            await self.reviews.reserve_decision(review_id, decision="approved" if body.approved else "rejected",
                                                reviewer=reviewer, note=body.note)
            return await self._resume(review_id)

    async def _resume(self, review_id: int) -> DeletionReviewResultResponse:
        row = await self._review(review_id)
        if row["decision"] is None:
            raise ReviewConflict("删除人工审核决定尚未确定")
        if row["result_message_id"] is None:
            self._token()
            if row["status"] == "pending":
                if row["decision"] == "approved":
                    try:
                        async with self.resources.delete_for_review(row) as exists:
                            if exists:
                                await self.files.retain_deleted(row["source_platform"], row["file_url"])
                            else:
                                # DELETE 已提交但 SQLite 尚未完成时，只认可已有保留原件，不移动新正式文件。
                                if await self.files.resources.exists(row["source_platform"], StoredFile(row["file_url"])):
                                    raise ReviewConflict("正式记录已移除但原件仍在正式目录，需要核对")
                            await self.files.read_deleted(row["source_platform"], row["file_url"])
                    except ResourceDeletionConflict as error:
                        await self.reviews.release_conflicting_approval(row)
                        raise ReviewConflict(str(error)) from None
                row = await self.reviews.complete_decision(review_id)
            # 两条分支均在本地存储完成之后发布，不因网络失败撤销已经完成的删除。
            await self._publish(row)
            row = await self._review(review_id)
        return DeletionReviewResultResponse.model_validate({
            "id": row["id"], "resource_id": int(row["resource_id"]), "status": row["status"],
            "approved": row["status"] == "approved", "reviewer": row["reviewer"], "reviewer_note": row["reviewer_note"],
            "reviewed_at": row["reviewed_at"], "result_message_id": row["result_message_id"],
        })

    async def _publish(self, row: dict) -> None:
        token = self._token()
        async def publish_and_record():
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result_id = await self.middleware.publish_deletion_result(
                        request_id=row["message_id"], target_platform_code=row["source_platform"], token=token,
                        approved=row["decision"] == "approved", note=row["reviewer_note"], reviewer=row["reviewer"],
                    )
                await self.reviews.mark_decision_published(row["id"], result_id)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 401:
                    self.sessions.invalidate_access_token(token)
                # 中间件 409 不包含旧结果正文，不能据此认定相同决定已经发布。
                raise
        task = asyncio.create_task(publish_and_record())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def start(self) -> None:
        if self.middleware is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name="deletion-review-recovery")

    async def _run(self) -> None:
        while True:
            try:
                for review_id in await self.reviews.list_unfinished_decisions():
                    try:
                        async with self._lock(review_id):
                            await self._resume(review_id)
                    except Exception as error:
                        logger.warning("删除人工审核恢复失败：review_id=%s error_type=%s", review_id, type(error).__name__)
            except Exception as error:
                logger.warning("删除审核恢复队列读取失败：error_type=%s", type(error).__name__)
            await asyncio.sleep(self.retry_interval_seconds)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
