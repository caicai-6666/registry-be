"""SQLite 仅保存待同步姓名的 userid，不复制员工资料或权限。"""
import asyncio
from uuid import uuid4

from app.infrastructure.sqlite import SQLiteDatabase


class SQLitePersonnelSyncRepository:
    def __init__(self,database: SQLiteDatabase):
        self.database=database
        self._ready=False
        self._lock=asyncio.Lock()

    async def initialize(self):
        async with self._lock:
            if self._ready:return
            async with self.database.transaction() as connection:
                await connection.execute('''CREATE TABLE IF NOT EXISTS personnel_name_sync (
                    userid TEXT COLLATE BINARY PRIMARY KEY NOT NULL CHECK(length(userid) BETWEEN 1 AND 128),
                    revision TEXT NOT NULL CHECK(length(revision)=32))''')
            self._ready=True

    async def enqueue(self,userids: list[str]):
        await self.initialize()
        if not userids:return
        async with self.database.transaction() as connection:
            await connection.executemany('''INSERT INTO personnel_name_sync(userid,revision) VALUES(?,?)
                ON CONFLICT(userid) DO UPDATE SET revision=excluded.revision''',[(userid,uuid4().hex) for userid in sorted(set(userids))])

    async def pending(self) -> list[tuple[str,str]]:
        await self.initialize()
        async with self.database.connection() as connection:
            async with connection.execute('SELECT userid,revision FROM personnel_name_sync ORDER BY userid') as cursor:
                return [(row['userid'],row['revision']) for row in await cursor.fetchall()]

    async def acknowledge(self,userid: str,revision: str):
        async with self.database.transaction() as connection:
            # 旧发送结果不得删除另一个刷新请求新登记的待同步项。
            await connection.execute('DELETE FROM personnel_name_sync WHERE userid=? AND revision=?',(userid,revision))
