"""人工入库审核、跨库文件晋升与持久化决定恢复。"""

import asyncio
import logging
from uuid import uuid4
from weakref import WeakValueDictionary

import httpx
from app.model.review import ReviewNotFound, ReviewConflict, ReviewUnavailable

from app.repository.resource import MySQLResourceRepository, ResourceApprovalConflict
from app.repository.review import SQLiteReviewRepository
from app.schema.ingestion_review import IngestionReviewDecision, IngestionReviewResultResponse
from app.service.middleware_session import MiddlewareSessionService
from app.service.review_files import ReviewFileService
from app.infrastructure.middleware import MiddlewareClient

logger = logging.getLogger(__name__)


class IngestionReviewService:
    def __init__(self, reviews: SQLiteReviewRepository, resources: MySQLResourceRepository,
                 files: ReviewFileService, sessions: MiddlewareSessionService, middleware: MiddlewareClient | None,
                 *, timeout_seconds: float = 40, retry_interval_seconds: float = 30):
        self.reviews, self.resources, self.files = reviews, resources, files
        self.sessions, self.middleware = sessions, middleware
        self.timeout_seconds, self.retry_interval_seconds = timeout_seconds, retry_interval_seconds
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
        self._task: asyncio.Task | None = None

    def _lock(self, review_id: int) -> asyncio.Lock:
        # 同一申请的 HTTP、附件读取与后台恢复串行执行；锁无引用后释放，不积累历史 ID。
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
            raise ReviewNotFound("入库审核申请不存在")
        return row

    async def read_pending_file(self, review_id: int) -> tuple[str, bytes]:
        async with self._lock(review_id):
            row = await self._review(review_id)
            if row["status"] != "pending" or row["decision"] is not None:
                raise ReviewConflict("该申请已确定审核决定，不能读取待审附件")
            if row["file_url"] is None:
                raise ReviewNotFound("待审文件原件不存在")
            return row["file_url"], await self.files.read_ingestion(row["source_platform"], row["file_url"])

    async def decide(self, review_id: int, body: IngestionReviewDecision, reviewer: str) -> IngestionReviewResultResponse:
        async with self._lock(review_id):
            row = await self._review(review_id)
            decision = "approved" if body.approved else "rejected"
            passport = (body.passport if body.passport is not None else row["passport"] or [uuid4().hex]) if body.approved else []
            if row["decision"] is None:
                if row["status"] != "pending":
                    raise ReviewConflict("该申请已经完成审核")
                self._token()
                if body.approved:
                    if row["file_url"] is None:
                        raise ReviewConflict("申请缺少原件信息，不能批准入库")
                    # 决定未保存时先确认原件有效；缺失附件不能产生无法执行的新批准决定。
                    await self.files.read_ingestion(row["source_platform"], row["file_url"])
            try:
                await self.reviews.reserve_decision(review_id, decision=decision, passport=passport, reviewer=reviewer, note=body.note, file_level=body.file_level)
            except ValueError as error:
                raise ReviewConflict(str(error)) from None
            return await self._resume(review_id)

    async def _resume(self, review_id: int) -> IngestionReviewResultResponse:
        row = await self._review(review_id)
        if row["decision"] is None:
            raise ReviewConflict("人工审核决定尚未确定")
        if row["result_message_id"] is None:
            if row["decision"] == "approved" and row["file_level"] is None:
                raise ReviewConflict("历史批准记录缺少文件等级，需先明确补齐后才能恢复")
            self._token()
            if row["status"] == "pending":
                if row["decision"] == "approved":
                    try:
                        async with self.resources.approve_ingestion(row) as resource_id:
                            await self.files.promote_ingestion(row["source_platform"], row["file_url"])
                            await self.files.read_resource(row["source_platform"], row["file_url"])
                    except ResourceApprovalConflict as error:
                        # 此异常仅在事务进入晋升体之前产生，未写入正式资源，可安全恢复待审。
                        await self.reviews.release_conflicting_approval(row)
                        raise ReviewConflict(str(error)) from None
                else:
                    resource_id = None
                row = await self.reviews.complete_decision(review_id, resource_id=resource_id)
            # 不因反馈失败撤销已确定决定或正式文件；只恢复未完成的发布阶段。
            await self._publish(row)
            row = await self._review(review_id)
        return IngestionReviewResultResponse.model_validate({
            "id": row["id"], "status": row["status"], "resource_id": int(row["resource_id"]) if row["resource_id"] else None,
            "passport": row["passport"], "file_level": row["file_level"], "reviewer": row["reviewer"], "reviewer_note": row["reviewer_note"],
            "reviewed_at": row["reviewed_at"], "result_message_id": row["result_message_id"],
        })

    async def _publish(self, row: dict) -> None:
        token = self._token()

        async def publish_and_record():
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    result_id = await self.middleware.publish_ingestion_result(
                        request_id=row["message_id"], target_platform_code=row["source_platform"], token=token,
                        note=row["reviewer_note"], reviewer=row["reviewer"], passport=row["passport"],
                        file_level=row["file_level"] if row["decision"] == "approved" else 0,
                    )
                await self.reviews.mark_decision_published(row["id"], result_id)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 401:
                    self.sessions.invalidate_access_token(token)
                # 409 没有旧反馈正文，不能证明内容一致，必须保留未完成凭据。
                raise

        # 请求取消或应用关闭时等待有界发布与本地回执写入，缩小发布成功但未记账的窗口。
        task = asyncio.create_task(publish_and_record())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def start(self) -> None:
        if self.middleware is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name="ingestion-review-recovery")

    async def _run(self) -> None:
        while True:
            try:
                for review_id in await self.reviews.list_unfinished_decisions():
                    try:
                        async with self._lock(review_id):
                            await self._resume(review_id)
                    except Exception as error:
                        logger.warning("入库人工审核恢复失败：review_id=%s error_type=%s", review_id, type(error).__name__)
            except Exception as error:
                logger.warning("入库审核恢复队列读取失败：error_type=%s", type(error).__name__)
            await asyncio.sleep(self.retry_interval_seconds)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
