"""将持久权限 outbox 送往中间件；不保存或重放旧权限载荷。"""
import asyncio
import logging

import httpx

from app.model.middleware_user import MiddlewareUserPatch

logger = logging.getLogger(__name__)


class PlatformPermissionService:
    def __init__(self, repository, sessions, middleware, *, retry_seconds=30, timeout_seconds=40):
        self.repository, self.sessions, self.middleware = repository, sessions, middleware
        self.retry_seconds, self.timeout_seconds = retry_seconds, timeout_seconds
        self._task = None

    async def publish(self, userid: str, *, only_pending=True):
        token = self.sessions.get_access_token()
        if token is None or self.middleware is None:
            raise RuntimeError('中间件会话尚未就绪')

        async def send(permissions):
            try:
                await self.middleware.update_user_info(MiddlewareUserPatch(userid=userid, permissions=permissions), token)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 401:
                    self.sessions.invalidate_access_token(token)
                raise
        await self.repository.publish(userid, send, timeout_seconds=self.timeout_seconds, only_pending=only_pending)

    async def run_once(self):
        for userid in await self.repository.pending():
            try:
                await self.publish(userid)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # 单人同步失败保留 outbox，不阻塞其他员工，不泄露权限与上游正文。
                logger.warning('平台文件权限同步失败，将重试：error_type=%s', type(error).__name__)

    async def _run(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning('平台文件权限恢复队列不可用：error_type=%s', type(error).__name__)
            await asyncio.sleep(self.retry_seconds)

    async def start(self):
        if self.middleware is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name='platform-permission-sync')

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
