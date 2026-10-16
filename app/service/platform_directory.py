"""定期查询中间件平台目录，在内存中原子替换公开信息与存活状态。"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from app.config.middleware import MiddlewareSettings
from app.config.platform_directory import PlatformDirectorySettings
from app.infrastructure.middleware import MiddlewareClient
from app.model.platform import PlatformDirectoryEntry, PlatformDirectorySnapshot
from app.service.middleware_session import MiddlewareSessionService

logger = logging.getLogger(__name__)


class PlatformDirectoryService:
    def __init__(
        self, settings: PlatformDirectorySettings, sessions: MiddlewareSessionService, *,
        middleware: MiddlewareSettings, client: MiddlewareClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._settings = settings
        self._sessions = sessions
        self._middleware = middleware
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._snapshot = PlatformDirectorySnapshot()
        self._updated_monotonic: float | None = None
        self._refresh_failed = False
        self._refresh_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._closed = False
        self._next_sync_at: datetime | None = None

    def get_snapshot(self) -> PlatformDirectorySnapshot:
        """同步读取不可变快照；未初始化、同步失败或超时均明确标为过期。"""
        stale = (self._closed or self._refresh_failed or self._updated_monotonic is None
                 or self._clock() - self._updated_monotonic >= self._settings.stale_after_seconds)
        return replace(self._snapshot, is_stale=stale, next_sync_at=self._next_sync_at)

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("平台目录服务已关闭")
        if self._settings.enabled and self._middleware.enabled and self._task is None:
            # 不等待登录或首次网络请求，平台查询故障不阻塞 Web 服务启动。
            self._next_sync_at = datetime.now(UTC)
            self._task = asyncio.create_task(self._run(), name="platform-directory")

    async def refresh(self) -> PlatformDirectorySnapshot:
        """完整校验响应后一次替换快照，手工刷新与周期刷新也保持串行。"""
        async with self._refresh_lock:
            if self._closed:
                raise RuntimeError("平台目录服务已关闭")
            token = self._sessions.get_access_token()
            if token is None:
                self._refresh_failed = True
                raise RuntimeError("中间件会话尚未就绪")
            try:
                if self._client is None:
                    self._client = MiddlewareClient(str(self._middleware.base_url),
                        timeout_seconds=self._middleware.request_timeout_seconds)
                async with asyncio.timeout(self._middleware.request_timeout_seconds):
                    platforms = await self._client.list_platforms(token)
            except Exception as exc:
                self._refresh_failed = True
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
                    # 只失效此请求使用的令牌，迟到的 401 不影响新登录会话。
                    self._sessions.invalidate_access_token(token)
                raise
            synced_at = datetime.now(UTC)
            previous = {platform.code: platform for platform in self._snapshot.platforms}
            entries = tuple(PlatformDirectoryEntry(
                **platform.model_dump(),
                # 失活只更新当前状态，保留最近一次观察到存活的时间，不推测实际下线时刻。
                last_alive_at=(synced_at if platform.is_alive else
                               previous[platform.code].last_alive_at if platform.code in previous else None),
            ) for platform in platforms)
            # 没有 await 的替换段不会向读者暴露半更新状态；空列表同样是有效快照。
            self._snapshot = PlatformDirectorySnapshot(entries, synced_at, is_stale=False)
            self._updated_monotonic = self._clock()
            self._refresh_failed = False
            return self.get_snapshot()

    async def _wait_for_next_sync(self, delay: float) -> None:
        # 与实际等待同时登记计划，失败重试不能使用成功刷新周期推算。
        self._next_sync_at = datetime.now(UTC) + timedelta(seconds=delay)
        try:
            await self._sleep(delay)
        finally:
            self._next_sync_at = None

    async def _run(self) -> None:
        try:
            while True:
                self._next_sync_at = None
                if self._sessions.get_access_token() is None:
                    self._refresh_failed = True
                    await self._wait_for_next_sync(self._settings.retry_interval_seconds)
                    continue
                try:
                    await self.refresh()
                    delay = self._settings.refresh_interval_seconds
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    logger.warning("平台目录刷新失败，将重试：error_type=%s status=%s", type(exc).__name__, status)
                    delay = self._settings.retry_interval_seconds
                await self._wait_for_next_sync(delay)
        finally:
            self._next_sync_at = None

    async def close(self) -> None:
        """取消并等待查询任务退出，然后关闭本服务拥有的 HTTP 客户端。"""
        self._closed = True
        self._next_sync_at = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        async with self._refresh_lock:
            if self._client is not None:
                await self._client.close()
                self._client = None
