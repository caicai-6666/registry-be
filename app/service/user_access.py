"""用户文件权限与管理员关系的持久化和中间件增量同步。"""
import asyncio
import logging

import httpx

from app.model.middleware_user import MiddlewareUserPatch
from app.model.user_access import UserAccessPatch, UserAccessState, UserAccessError, apply_access_patch
from app.repository.employee import MySQLEmployeeRepository
from app.repository.user_access_sync import SQLiteUserAccessSyncRepository
from app.infrastructure.middleware import MiddlewareClient
from app.service.middleware_session import MiddlewareSessionService
from app.service.platform_directory import PlatformDirectoryService

logger=logging.getLogger(__name__)


class UserAccessService:
    def __init__(self,employees: MySQLEmployeeRepository,pending: SQLiteUserAccessSyncRepository,
                 sessions: MiddlewareSessionService,middleware: MiddlewareClient | None,
                 directory: PlatformDirectoryService,*,timeout_seconds: float=40):
        self._employees,self._pending=employees,pending
        self._sessions,self._middleware,self._directory=sessions,middleware,directory
        self._timeout=timeout_seconds
        self._lock=asyncio.Lock()

    def _validate_platforms(self,patch: UserAccessPatch,current: UserAccessState) -> None:
        snapshot=self._directory.get_snapshot()
        if snapshot.is_stale:
            raise UserAccessError('平台目录尚未就绪或已过期',phase='validation')
        known={platform.code for platform in snapshot.platforms}
        for code,level in patch.platform_permissions.items():
            # 隐藏/已下线目录的平台仍允许撤销已有授权，不能借此新增未知平台授权。
            if code not in known and not (level is None and code in current.platform_permissions):
                raise UserAccessError('文件权限的平台不在当前可管理目录中',status_code=422,phase='validation')
        for code,enabled in patch.platform_admins.items():
            if code not in known and not (not enabled and code in current.managed_platforms):
                raise UserAccessError('管理员变更的平台不在当前可管理目录中',status_code=422,phase='validation')

    async def _sync(self,userid: str) -> None:
        jobs=await self._pending.pending(userid)
        for kind,code,revision in jobs:
            token=self._sessions.get_access_token()
            if token is None or self._middleware is None:continue
            current=await self._employees.get_user_access(userid)
            try:
                if kind == 'permissions':
                    # 中间件 permissions 是整体替换，必须发送合并后的完整当前映射。
                    await self._middleware.update_user_info(
                        MiddlewareUserPatch(userid=userid,permissions=current.platform_permissions),token)
                else:
                    await self._middleware.set_platform_admin(code,userid,code in current.managed_platforms,token)
            except Exception as error:
                status=error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None
                if status == 401:self._sessions.invalidate_access_token(token)
                logger.warning('用户权限增量同步失败：kind=%s error_type=%s status=%s',kind,type(error).__name__,status)
                continue
            await self._pending.acknowledge(userid,kind,code,revision)

    async def update(self,patch: UserAccessPatch) -> UserAccessState:
        phase,database_updated='validation',False
        try:
            async with asyncio.timeout(self._timeout),self._lock:
                current=await self._employees.get_user_access(patch.userid)
                self._validate_platforms(patch,current)
                proposed=apply_access_patch(current,patch)
                permissions_changed=proposed.platform_permissions != current.platform_permissions
                old_admins=set(current.managed_platforms)
                admin_codes=[code for code,enabled in patch.platform_admins.items() if (code in old_admins) != enabled]
                phase='prepare_sync'
                # 先保留可恢复的标记，再提交 MySQL；不将客户端旧载荷作为后续重试真值。
                await self._pending.enqueue(patch.userid,permissions=permissions_changed,admin_codes=admin_codes)
                phase,database_updated='database',None
                await self._employees.update_user_access(patch,current)
                database_updated=True
                phase='middleware_sync'
                await self._sync(patch.userid)
                pending=len(await self._pending.pending(patch.userid))
                if pending:
                    raise UserAccessError('数据库已更新，部分权限尚未同步；重复提交可重试',phase=phase,
                                          database_updated=True,pending_updates=pending)
                return await self._employees.get_user_access(patch.userid)
        except UserAccessError as error:
            if phase == 'middleware_sync' and not error.database_updated:
                raise UserAccessError('数据库已更新，同步阶段未完成，请核对当前用户并重试',phase=phase,database_updated=True) from None
            raise
        except Exception as error:
            logger.warning('用户权限更新失败：phase=%s error_type=%s',phase,type(error).__name__)
            raise UserAccessError('用户权限更新暂不可用，请稍后重试',phase=phase,database_updated=database_updated) from None
