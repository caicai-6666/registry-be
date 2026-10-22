"""查询当前平台管理员与展示资料；不以文件访问等级授予管理权。"""

import asyncio
import logging

import httpx

from app.auth.error import AuthUnavailable
from app.auth.model import AuthenticatedUser
from app.model.platform_admin import PlatformAdminProfile
from app.config.authorization import AuthorizationSettings
from app.infrastructure.middleware import MiddlewareClient
from app.repository.employee import MySQLEmployeeRepository
from app.service.middleware_session import MiddlewareSessionService

logger = logging.getLogger(__name__)


class PlatformAdminService:
    def __init__(self, settings: AuthorizationSettings, employees: MySQLEmployeeRepository,
                 sessions: MiddlewareSessionService, client: MiddlewareClient | None):
        self._settings, self._employees = settings, employees
        self._sessions, self._client = sessions, client
        self._closed = False

    def _log_failure(self, message, error, token):
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        if status == 401:
            self._sessions.invalidate_access_token(token)
        logger.warning('%s：error_type=%s status=%s', message, type(error).__name__, status)

    async def list_admin_profiles(self) -> dict[str, list[PlatformAdminProfile]]:
        """展示各平台管理员名称与头像；中间件只支持查询自身管理员，故从本地关联读取。"""
        if self._closed:
            raise AuthUnavailable('平台管理员资料暂不可用')
        try:
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                profiles = {code: list(values) for code, values in
                         (await self._employees.list_platform_admin_profiles()).items()}
            return profiles
        except Exception as error:
            logger.warning('平台管理员资料查询失败：error_type=%s', type(error).__name__)
            raise AuthUnavailable('平台管理员资料暂不可用') from None

    async def is_current_platform_admin(self, user: AuthenticatedUser) -> bool:
        """每次向中央查询当前平台管理员，不根据本地身份展示字段放行。"""
        if self._closed:
            raise AuthUnavailable('平台管理员服务暂不可用')
        token = self._sessions.get_access_token()
        if token is None or self._client is None:
            raise AuthUnavailable('平台管理员服务暂不可用')
        try:
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                userids = await self._client.list_current_platform_admins(token)
        except Exception as error:
            self._log_failure('平台管理员查询失败', error, token)
            raise AuthUnavailable('平台管理员服务暂不可用') from None
        return user.userid in userids

    async def close(self) -> None:
        self._closed = True
