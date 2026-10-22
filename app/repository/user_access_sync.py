"""权限与管理员增量同步的持久标记，发布时始终读取 MySQL 当前状态。"""
import asyncio
from uuid import uuid4

from app.infrastructure.sqlite import SQLiteDatabase


class SQLiteUserAccessSyncRepository:
    def __init__(self,database: SQLiteDatabase):
        self.database=database
        self._ready=False
        self._lock=asyncio.Lock()

    async def initialize(self):
        async with self._lock:
            if self._ready:return
            async with self.database.transaction() as connection:
                await connection.execute('''CREATE TABLE IF NOT EXISTS user_access_sync (
                    userid TEXT COLLATE BINARY NOT NULL CHECK(length(userid) BETWEEN 1 AND 128),
                    kind TEXT NOT NULL CHECK(kind IN ('permissions','admin')),
                    platform_code TEXT NOT NULL,
                    revision TEXT NOT NULL CHECK(length(revision)=32),
                    PRIMARY KEY(userid,kind,platform_code),
                    CHECK((kind='permissions' AND platform_code='') OR (kind='admin' AND length(platform_code)>0)))''')
            self._ready=True

    async def enqueue(self,userid: str,*,permissions: bool,admin_codes: list[str]):
        await self.initialize()
        jobs=([('permissions','')] if permissions else [])+[('admin',code) for code in sorted(set(admin_codes))]
        if not jobs:return
        async with self.database.transaction() as connection:
            await connection.executemany('''INSERT INTO user_access_sync(userid,kind,platform_code,revision) VALUES(?,?,?,?)
                ON CONFLICT(userid,kind,platform_code) DO UPDATE SET revision=excluded.revision''',
                [(userid,kind,code,uuid4().hex) for kind,code in jobs])

    async def pending(self,userid: str) -> list[tuple[str,str,str]]:
        await self.initialize()
        async with self.database.connection() as connection:
            async with connection.execute('SELECT kind,platform_code,revision FROM user_access_sync WHERE userid=? '
                                          'ORDER BY kind,platform_code',(userid,)) as cursor:
                return [(row['kind'],row['platform_code'],row['revision']) for row in await cursor.fetchall()]

    async def acknowledge(self,userid: str,kind: str,code: str,revision: str):
        async with self.database.transaction() as connection:
            await connection.execute('DELETE FROM user_access_sync WHERE userid=? AND kind=? AND platform_code=? AND revision=?',
                                     (userid,kind,code,revision))
