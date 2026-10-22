"""管理员调整文件等级：MySQL 更新、流发布和 SQLite 审计恢复。"""
import asyncio
import logging

import httpx

from app.infrastructure.middleware import MiddlewareClient
from app.repository.resource import MySQLResourceRepository
from app.repository.file_level_adjustment import SQLiteFileLevelAdjustmentRepository
from app.service.middleware_session import MiddlewareSessionService
from app.model.file_level_adjustment import (
    FileLevelAdjustmentError, FileLevelAdjustmentRequest, FileLevelAdjustmentResponse,
    FileLevelAdjustmentHistoryResponse,
)

logger = logging.getLogger(__name__)


class FileLevelAdjustmentService:
    def __init__(self, resources: MySQLResourceRepository, logs: SQLiteFileLevelAdjustmentRepository,
                 sessions: MiddlewareSessionService, middleware: MiddlewareClient | None, *, timeout_seconds: float = 40):
        self.resources, self.logs, self.sessions, self.middleware = resources, logs, sessions, middleware
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    @staticmethod
    def _response(row: dict) -> FileLevelAdjustmentResponse:
        fields = ('id', 'previous_file_level', 'file_level', 'reviewer', 'note', 'status', 'result_message_id', 'created_at')
        return FileLevelAdjustmentResponse(resource_id=int(row['resource_id']), **{key: row[key] for key in fields})

    async def list_history(self) -> list[FileLevelAdjustmentHistoryResponse]:
        rows = await self.logs.list_history()
        return [FileLevelAdjustmentHistoryResponse.model_validate(row) for row in rows]

    async def adjust(self, body: FileLevelAdjustmentRequest, reviewer: str, reviewer_name: str) -> FileLevelAdjustmentResponse:
        row, updated = None, False
        try:
            async with asyncio.timeout(self.timeout_seconds), self._lock:
                row = await self.logs.latest(body.resource_id)
                updated = True if row and row['status'] == 'local_updated' else False
                same = row and (row['file_level'], row['reviewer'], row['note']) == (body.file_level, reviewer, body.note)
                if row and row['status'] != 'published' and not same:
                    raise FileLevelAdjustmentError('该文件有未完成调整，请先以原参数重试或核对日志', status_code=409,
                                                   log_id=row['id'], database_updated=None)
                token = self.sessions.get_access_token()
                if self.middleware is None or token is None:
                    raise FileLevelAdjustmentError('中间件会话尚未就绪，请稍后重试',
                        log_id=row['id'] if row and row['status'] != 'published' else None, database_updated=updated)
                resource = await self.resources.find_by_id(body.resource_id)
                if resource is None:
                    raise FileLevelAdjustmentError('正式文件不存在', status_code=404)
                if not resource.source_id:
                    raise FileLevelAdjustmentError('文件缺少来源 ID，无法发布等级调整', status_code=409)
                if not row or row['status'] == 'published':
                    if resource.file_level == body.file_level:
                        if same:
                            return self._response(row)
                        raise FileLevelAdjustmentError('文件已是该等级，无需调整', status_code=409)
                    row = await self.logs.create(resource, body, reviewer, reviewer_name)
                # prepared 可能是提交前失败，也可能是 MySQL 已提交但 SQLite 尚未确认。
                # 仓库仅允许原等级或目标等级，且必须是同一正式文件。
                updated = None
                await self.resources.adjust_file_level(row)
                updated = True
                await self.logs.record(row['id'], status='local_updated')
                row['status'] = 'local_updated'
                message_id = await self.middleware.publish_file_level_adjustment(
                    source_id=row['source_id'], target_platform_code=row['source_platform'], file_level=row['file_level'],
                    note=row['note'], reviewer=row['reviewer_name'], token=token)
                await self.logs.record(row['id'], status='published', message_id=message_id)
                row.update(status='published', result_message_id=message_id)
                return self._response(row)
        except FileLevelAdjustmentError as error:
            if row and row['status'] != 'published' and error.log_id is None:
                raise FileLevelAdjustmentError(str(error), status_code=error.status_code,
                    log_id=row['id'], database_updated=updated) from None
            raise
        except Exception as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            if status == 401:
                self.sessions.invalidate_access_token(token)
            logger.warning('文件等级调整失败：log_id=%s error_type=%s status=%s',
                           row['id'] if row else None, type(error).__name__, status)
            if row and row['status'] != 'published':
                # 仅保存异常类型及状态码，禁止持久化凭据、SQL 或上游错误正文。
                try:
                    await self.logs.record(row['id'], error=f'{type(error).__name__}:{status}')
                except Exception:
                    logger.warning('文件等级调整错误日志更新失败：log_id=%s', row['id'])
            raise FileLevelAdjustmentError('文件等级调整未全部完成，请核对日志并以原参数重试',
                log_id=row['id'] if row else None, database_updated=updated) from None
