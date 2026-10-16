"""持续接收无附件删除申请，暂存审核或发布拒绝后确认消费。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from app.config.deletion import DeletionSettings
from app.infrastructure.middleware import MiddlewareClient
from app.infrastructure.resource_store import ResourceFileStore
from app.model.passport import same_passports
from app.model.deletion import DeletionDelivery, DeletionResource
from app.model.resource import StoredFile
from app.repository.deletion_review import DeletionReviewConflict, SQLiteDeletionReviewRepository
from app.repository.resource import MySQLResourceRepository
from app.service.middleware_session import MiddlewareSessionService

logger = logging.getLogger(__name__)


class DeletionConsumerService:
    def __init__(
        self, settings: DeletionSettings, sessions: MiddlewareSessionService,
        client: MiddlewareClient, repository: MySQLResourceRepository, files: ResourceFileStore, *,
        reviews: SQLiteDeletionReviewRepository, request_timeout_seconds: float = 40,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._settings = settings
        self._sessions = sessions
        self._client = client
        self._repository = repository
        self._files = files
        self._reviews = reviews
        self._request_timeout_seconds = request_timeout_seconds
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self._pending: DeletionDelivery | None = None
        self._phase = "initialize"
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("删除接收服务已关闭")
        if self._settings.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="deletion-consumer")

    async def _request(self, method, *args, **kwargs):
        token = self._sessions.get_access_token()
        if token is None:
            raise RuntimeError("中间件会话尚未就绪")
        try:
            async with asyncio.timeout(self._request_timeout_seconds):
                return await method(*args, token=token, **kwargs)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                self._sessions.invalidate_access_token(token)
            raise

    async def _verify_file(self, delivery: DeletionDelivery, resource: DeletionResource) -> None:
        message = delivery.message
        if ((resource.source_platform, resource.source_id) != (message.platform_code, message.source_id)
                or not same_passports(resource.passport, message.passport)):
            raise ValueError("已保存删除审核引用的正式资源身份发生变化")
        if not await self._files.exists(message.platform_code, StoredFile(resource.file_url)):
            raise FileNotFoundError("正式资源原文件缺失，不能完成删除申请接收")

    async def process(self, delivery: DeletionDelivery) -> None:
        if self._closed:
            raise RuntimeError("删除接收服务已关闭")
        message = delivery.message
        self._phase = "review_lookup"
        review = await self._reviews.find(delivery)
        if review is not None and review.get("decision") is not None:
            # 人工决定由审核恢复器执行和反馈；重投不再查询目标、补文件或触发自动拒绝。
            await self._acknowledge(delivery)
            return
        if review is not None and review["status"] == "rejected":
            await self._reject(delivery, review["reviewer_note"])
            return
        if review is None or review["status"] == "pending":
            self._phase = "lookup"
            resource = await self._repository.find_deletion_source(message.platform_code, message.source_id)
            if review is None:
                if resource is None:
                    await self._reject(delivery, "## 删除申请拒绝\n\n申请平台的 source_id 在注册中心正式资源中不存在，无法删除。")
                    return
                if not same_passports(resource.passport, message.passport):
                    await self._reject(delivery, "## 删除申请拒绝\n\n申请通行证与正式资源的通行证不一致，无法删除。")
                    return
            else:
                # 已保存申请不能因重投变成另一条目标或改写人工审核结果。
                if resource is None or (str(resource.resource_id), resource.file_url) != (
                    review["resource_id"], review["file_url"],
                ):
                    raise ValueError("已保存删除申请的正式资源引用无法核对")
            self._phase = "verify_file"
            await self._verify_file(delivery, resource)
            self._phase = "persist_review"
            try:
                await self._reviews.save_pending(delivery, resource)
            except DeletionReviewConflict:
                await self._reject(delivery, "## 删除申请拒绝\n\n该正式资源已有删除审核记录，不能重复建立删除申请。")
                return
        # approved 的重投仅恢复 ACK；删除后原资源可能已不存在，无须补回原文件。
        await self._acknowledge(delivery)

    async def _reject(self, delivery: DeletionDelivery, note: str) -> None:
        self._phase = "persist_rejection"
        await self._reviews.save_rejected(delivery, note)
        review = await self._reviews.find(delivery)
        if review["result_message_id"] is None:
            self._phase = "publish_rejection"
            result_id = await self._request(self._client.publish_deletion_rejection, delivery, note=note)
            await self._reviews.mark_rejection_published(delivery, result_id)
        await self._acknowledge(delivery)

    async def _acknowledge(self, delivery: DeletionDelivery) -> None:
        review = await self._reviews.find(delivery)
        if review is None or (review["status"] == "rejected" and review["result_message_id"] is None and review.get("decision") is None):
            raise ValueError("删除申请未保存或拒绝结果未确认发布，不能 ACK")
        if review["status"] == "pending" and (review["resource_id"] is None or review["file_url"] is None):
            raise ValueError("待删除审核缺少正式资源引用，不能 ACK")
        self._phase = "ack"
        await self._request(self._client.acknowledge_deletion, delivery)
        logger.info("删除申请已接收：message_id=%s platform=%s", delivery.message.message_id, delivery.message.platform_code)

    async def _run(self) -> None:
        while True:
            try:
                if self._sessions.get_access_token() is None:
                    await self._sleep(self._settings.poll_interval_seconds)
                    continue
                if self._pending is None:
                    self._phase = "pull"
                    self._pending = await self._request(self._client.pull_deletion)
                if self._pending is None:
                    await self._sleep(self._settings.poll_interval_seconds)
                    continue
                await self.process(self._pending)
                self._pending = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if status == 409 and self._phase == "ack":
                    self._pending = None
                # 发布 409 不能证明先前结果内容一致，不跳过当前消息，也不 ACK。
                logger.warning("删除申请接收失败，将重试：phase=%s error_type=%s status=%s",
                               self._phase, type(exc).__name__, status)
                await self._sleep(self._settings.retry_interval_seconds)

    async def close(self) -> None:
        """停止消费；共享 HTTP、MySQL 和 SQLite 由应用随后关闭。"""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._pending = None
