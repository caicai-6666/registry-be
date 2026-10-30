"""知识后端头像 HTTP 代理，不保存头像或用户凭据。"""
import asyncio
import logging
import re

import httpx

from app.config.knowledge import KnowledgeSettings
from app.config.api import APPLICATION_PREFIX

logger = logging.getLogger(__name__)


class AvatarProxyError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__('头像代理请求未成功')


class KnowledgeClient:
    def __init__(self, settings: KnowledgeSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        if settings.base_url is None:
            raise ValueError('知识后端地址尚未配置')
        self._settings = settings
        self._http = httpx.AsyncClient(base_url=str(settings.base_url),
            timeout=settings.timeout_seconds, follow_redirects=False, trust_env=False, transport=transport)

    @property
    def avatar_cache_seconds(self) -> int:
        return self._settings.avatar_cache_seconds

    async def read_avatar(self, filename: str, access_token: str) -> bytes:
        if re.fullmatch(r'[a-f0-9]{32}\.webp', filename) is None:
            raise AvatarProxyError(422)
        try:
            # 固定目标和路径，仅透传本次用户 token；不携带平台凭据、Cookie 或任意请求头。
            async with asyncio.timeout(self._settings.timeout_seconds):
                request = self._http.build_request('GET',
                    f'{APPLICATION_PREFIX}/api/resource/avatar/{filename}',
                    headers={'Authorization': f'Bearer {access_token}'})
                # 共享连接池不共享用户 Cookie，连上游历史 Set-Cookie 也不再次发送。
                request.headers.pop('cookie', None)
                response = await self._http.send(request, stream=True)
                try:
                    if response.status_code in (401, 403, 404, 422):
                        raise AvatarProxyError(response.status_code)
                    media_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if response.status_code != 200 or media_type != 'image/webp':
                        raise ValueError('头像上游响应不合法')
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > self._settings.avatar_max_bytes:
                            raise ValueError('头像响应超出大小上限')
                        content.extend(chunk)
                    if not content:
                        raise ValueError('头像响应为空')
                    return bytes(content)
                finally:
                    await response.aclose()
        except AvatarProxyError:
            raise
        except Exception as error:
            logger.warning('头像代理失败：error_type=%s', type(error).__name__)
            raise AvatarProxyError(503) from None

    async def close(self) -> None:
        await self._http.aclose()
