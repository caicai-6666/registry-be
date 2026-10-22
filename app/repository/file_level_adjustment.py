"""文件等级调整审计日志；先记录意图，完成每个外部步骤后保存确认。"""
from datetime import datetime, timezone

from app.infrastructure.sqlite import SQLiteDatabase
from app.model.file_level_adjustment import FileLevelAdjustmentError, FileLevelAdjustmentRequest
from app.model.resource import StoredResource


class SQLiteFileLevelAdjustmentRepository:
    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def initialize(self):
        # 独立附属日志表按需创建，不重建审核表或改变既有审核 schema 版本。
        async with self.database.transaction() as connection:
            await connection.execute('''CREATE TABLE IF NOT EXISTS file_level_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                source_id TEXT NOT NULL,
                file_url TEXT NOT NULL,
                previous_file_level INTEGER NULL CHECK(previous_file_level IS NULL OR typeof(previous_file_level)='integer'),
                file_level INTEGER NOT NULL CHECK(typeof(file_level)='integer'),
                reviewer TEXT NOT NULL,
                reviewer_name TEXT NOT NULL,
                note TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','local_updated','published')),
                result_message_id TEXT NULL,
                last_error TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)''')
            await connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_file_level_adjustments_pending "
                                     "ON file_level_adjustments(resource_id) WHERE status!='published'")

    async def latest(self, resource_id: int) -> dict | None:
        await self.initialize()
        async with self.database.connection() as connection:
            async with connection.execute('SELECT * FROM file_level_adjustments WHERE resource_id=? ORDER BY id DESC LIMIT 1',
                                          (str(resource_id),)) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_history(self) -> list[dict]:
        """所有管理员共享全量历史，包含未完成意图；不依赖正式资源仍然存在。"""
        await self.initialize()
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT id,resource_id,source_platform,source_id,previous_file_level,file_level,"
                "reviewer,reviewer_name,note,status,result_message_id,created_at,updated_at "
                "FROM file_level_adjustments ORDER BY created_at DESC,id DESC"
            ) as cursor:
                return [{**dict(row), "resource_id": int(row["resource_id"])} for row in await cursor.fetchall()]

    async def create(self, resource: StoredResource, body: FileLevelAdjustmentRequest,
                     reviewer: str, reviewer_name: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        async with self.database.transaction() as connection:
            # SQLite 唯一索引阻止其他请求覆盖未完成调整。
            async with connection.execute('SELECT id FROM file_level_adjustments WHERE resource_id=? AND status!=?',
                                          (str(body.resource_id), 'published')) as cursor:
                if await cursor.fetchone():
                    raise FileLevelAdjustmentError('该文件已有未完成等级调整', status_code=409)
            await connection.execute('''INSERT INTO file_level_adjustments
                (resource_id,source_platform,source_id,file_url,previous_file_level,file_level,reviewer,reviewer_name,note,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (str(body.resource_id), resource.source_platform, resource.source_id, resource.file.file_url,
                 resource.file_level, body.file_level, reviewer, reviewer_name, body.note, now, now))
        return await self.latest(body.resource_id)

    async def record(self, log_id: int, *, status: str | None = None, message_id: str | None = None,
                     error: str | None = None):
        async with self.database.transaction() as connection:
            await connection.execute('UPDATE file_level_adjustments SET status=COALESCE(?,status),'
                'result_message_id=COALESCE(?,result_message_id),last_error=?,updated_at=? WHERE id=?',
                (status, message_id, error, datetime.now(timezone.utc).isoformat(), log_id))
