"""直接以用户 token 查询中央会话，取得中央用户名；不签发或缓存登录。"""

import asyncio
import logging

import httpx

from app.auth.error import AuthUnavailable
from app.auth.model import AuthenticatedUser
from app.infrastructure.middleware import MiddlewareClient
from app.service.middleware_session import MiddlewareSessionService

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, sessions: MiddlewareSessionService, client: MiddlewareClient | None,
                 *, timeout_seconds: float = 5):
        self._sessions, self._client = sessions, client
        self._timeout_seconds = timeout_seconds

    async def resolve_user(self, access_token: str) -> AuthenticatedUser | None:
        """每次请求都验证中央 token，避免本地缓存绕过重登撤销或中央续期。"""
        if not access_token or not access_token.strip():
            return None
        platform_token = self._sessions.get_access_token()
        if self._client is None or platform_token is None:
            raise AuthUnavailable('中间件用户认证暂不可用')
        try:
            async with asyncio.timeout(self._timeout_seconds):
                status = await self._client.touch_user_login(access_token, platform_token)
                if not status.is_logged_in:
                    return None
                # 身份和名称均以中央查询为准；模拟用户无需在本地员工表存在。
                return AuthenticatedUser(userid=status.userid, name=status.username)
        except Exception as error:
            code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            if code == 401:
                self._sessions.invalidate_access_token(platform_token)
            logger.warning('中央用户认证失败：error_type=%s status=%s', type(error).__name__, code)
            raise AuthUnavailable('中间件用户认证暂不可用') from None
