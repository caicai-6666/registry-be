"""服务间原件读取认证；平台身份只能来自中间件实时验证结果。"""

import asyncio
import logging

import httpx
from pydantic import SecretStr

from app.infrastructure.middleware import MiddlewareClient
from app.service.middleware_session import MiddlewareSessionService

logger = logging.getLogger(__name__)


class ServiceFileAuthError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ServiceFileAuth:
    def __init__(self, sessions: MiddlewareSessionService, client: MiddlewareClient | None, *, timeout_seconds: float = 5):
        self._sessions = sessions
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def authorize(self, access_token: str) -> None:
        token = self._sessions.get_access_token()
        if self._client is None or token is None:
            raise ServiceFileAuthError('平台认证服务暂不可用', 503)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                platform = await self._client.resolve_platform(SecretStr(access_token), token)
        except Exception as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            if status == 401:
                # 上游 401 指 registry 自身凭据失效，不能误判调用方 token 无效。
                self._sessions.invalidate_access_token(token)
            logger.warning('服务间原件认证失败：error_type=%s status=%s', type(error).__name__, status)
            raise ServiceFileAuthError('平台认证服务暂不可用', 503) from None
        if not platform.valid:
            raise ServiceFileAuthError('平台令牌无效或已过期', 401)
        if platform.code != 'knowledge-accumulation':
            raise ServiceFileAuthError('当前平台无权代用户读取正式文件', 403)
