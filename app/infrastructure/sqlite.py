"""SQLite 异步连接与事务生命周期；业务审核写入由仓库负责。"""

import asyncio
from contextlib import asynccontextmanager
from math import ceil
from pathlib import Path

import aiosqlite

from app.config.sqlite import SQLiteSettings
from app.model.passport import normalize_passports, encode_passports

_SCHEMA_PATH = Path(__file__).with_name("sqlite_schema.sql")
_SCHEMA_VERSION = 8


class SQLiteDatabase:
    def __init__(self, settings: SQLiteSettings):
        self.settings = settings
        self._connection: aiosqlite.Connection | None = None
        self._initialize_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._closed = False

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._closed:
                raise RuntimeError("SQLite 数据库已关闭")
            if self._connection is not None:
                return
            await asyncio.to_thread(self.settings.path.parent.mkdir, parents=True, exist_ok=True)
            connection = await aiosqlite.connect(
                str(self.settings.path), timeout=self.settings.busy_timeout_seconds, isolation_level=None,
            )
            try:
                connection.row_factory = aiosqlite.Row
                await connection.execute(f"PRAGMA busy_timeout={ceil(self.settings.busy_timeout_seconds * 1000)}")
                await connection.execute("PRAGMA foreign_keys=ON")
                async with connection.execute("PRAGMA user_version") as cursor:
                    version = (await cursor.fetchone())[0]
                if version not in (0, 1, 2, 3, 4, 5, 6, 7, _SCHEMA_VERSION):
                    raise RuntimeError("SQLite 数据库版本不受当前应用支持")
                async with connection.execute("PRAGMA journal_mode=WAL") as cursor:
                    if (await cursor.fetchone())[0].lower() != "wal":
                        raise RuntimeError("SQLite 存储不支持 WAL 模式")
                await connection.execute("PRAGMA synchronous=FULL")
                if version == 0:
                    schema = await asyncio.to_thread(_SCHEMA_PATH.read_text)
                    await connection.executescript(schema)
                elif version == 1:
                    # 旧版基础设施升级为资源一一关联；冲突时整段回滚，不删除审核记录。
                    await connection.executescript("""
                        BEGIN IMMEDIATE;
                        ALTER TABLE ingestion_reviews ADD COLUMN request_offset TEXT NULL
                            CHECK(request_offset IS NULL OR (length(request_offset) BETWEEN 1 AND 20 AND request_offset NOT GLOB '*[^0-9]*'));
                        ALTER TABLE ingestion_reviews ADD COLUMN result_message_id TEXT NULL
                            CHECK(result_message_id IS NULL OR length(result_message_id) BETWEEN 1 AND 128);
                        ALTER TABLE deletion_reviews ADD COLUMN request_offset TEXT NULL
                            CHECK(request_offset IS NULL OR (length(request_offset) BETWEEN 1 AND 20 AND request_offset NOT GLOB '*[^0-9]*'));
                        ALTER TABLE deletion_reviews ADD COLUMN result_message_id TEXT NULL
                            CHECK(result_message_id IS NULL OR length(result_message_id) BETWEEN 1 AND 128);
                        DROP INDEX IF EXISTS idx_ingestion_reviews_resource;
                        DROP INDEX IF EXISTS idx_deletion_reviews_resource;
                        CREATE UNIQUE INDEX uq_ingestion_reviews_resource ON ingestion_reviews(resource_id);
                        CREATE UNIQUE INDEX uq_deletion_reviews_resource ON deletion_reviews(resource_id);
                        PRAGMA user_version=2;
                        COMMIT;
                    """)
                if version in (1, 2):
                    await connection.executescript("""
                        BEGIN IMMEDIATE;
                        ALTER TABLE ingestion_reviews ADD COLUMN file_url TEXT NULL
                            CHECK(file_url IS NULL OR length(file_url) BETWEEN 1 AND 146);
                        ALTER TABLE ingestion_reviews ADD COLUMN name TEXT NULL
                            CHECK(name IS NULL OR length(name) BETWEEN 1 AND 200);
                        ALTER TABLE ingestion_reviews ADD COLUMN abstract TEXT NULL
                            CHECK(abstract IS NULL OR length(abstract) BETWEEN 1 AND 10000);
                        ALTER TABLE ingestion_reviews ADD COLUMN embedding TEXT NULL
                            CHECK(embedding IS NULL OR CASE WHEN json_valid(embedding) THEN json_type(embedding)='array' AND json_array_length(embedding)>0 ELSE 0 END);
                        ALTER TABLE ingestion_reviews ADD COLUMN passport_recommendations TEXT NULL
                            CHECK(passport_recommendations IS NULL OR CASE WHEN json_valid(passport_recommendations) THEN json_type(passport_recommendations)='array' ELSE 0 END);
                        CREATE UNIQUE INDEX uq_ingestion_reviews_pending_source
                            ON ingestion_reviews(source_platform,source_id) WHERE status='pending';
                        PRAGMA user_version=3;
                        COMMIT;
                    """)
                if version in (1, 2, 3):
                    # 删除契约没有来源审核员；存在历史值时不能静默丢弃审计信息。
                    await connection.execute("BEGIN IMMEDIATE")
                    async with connection.execute("SELECT 1 FROM deletion_reviews WHERE source_reviewer IS NOT NULL LIMIT 1") as cursor:
                        if await cursor.fetchone() is not None:
                            raise RuntimeError("旧删除审核包含来源审核员信息，需要显式迁移后再升级")
                    await connection.execute("ALTER TABLE deletion_reviews ADD COLUMN passport TEXT NULL CHECK(passport IS NULL OR length(passport) BETWEEN 1 AND 512)")
                    await connection.execute("ALTER TABLE deletion_reviews ADD COLUMN file_url TEXT NULL CHECK(file_url IS NULL OR length(file_url) BETWEEN 1 AND 146)")
                    await connection.execute("ALTER TABLE deletion_reviews DROP COLUMN source_reviewer")
                    await connection.execute("PRAGMA user_version=4")
                    await connection.commit()
                if version in (1, 2, 3, 4):
                    # 决定先持久化，再执行跨库、文件和反馈操作；旧结论不自动重新发布。
                    await connection.executescript("""
                        BEGIN IMMEDIATE;
                        ALTER TABLE ingestion_reviews ADD COLUMN decision TEXT NULL
                            CHECK(decision IS NULL OR decision IN ('approved','rejected'));
                        ALTER TABLE ingestion_reviews ADD COLUMN passport TEXT NULL
                            CHECK(passport IS NULL OR length(passport) <= 512);
                        PRAGMA user_version=5;
                        COMMIT;
                    """)
                if version in (1, 2, 3, 4, 5):
                    # 删除也预留人工决定；历史结论保持 NULL，避免启动后误重复发布。
                    await connection.executescript("""
                        BEGIN IMMEDIATE;
                        ALTER TABLE deletion_reviews ADD COLUMN decision TEXT NULL
                            CHECK(decision IS NULL OR decision IN ('approved','rejected'));
                        PRAGMA user_version=6;
                        COMMIT;
                    """)
                if version in (1, 2, 3, 4, 5, 6):
                    await self._upgrade_passports(connection)
                if version in (1, 2, 3, 4, 5, 6, 7):
                    await self._upgrade_deletion_snapshot(connection)
                # 不把缺表的受损数据库误判为已成功初始化。
                async with connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ingestion_reviews','deletion_reviews')") as cursor:
                    if len(await cursor.fetchall()) != 2:
                        raise RuntimeError("SQLite 审核表不完整")
                self._connection = connection
            except BaseException:
                await connection.rollback()
                await connection.close()
                raise

    @staticmethod
    async def _upgrade_deletion_snapshot(connection) -> None:
        """补充删除文件快照；旧记录只从同一正式资源的已批准入库记录恢复描述。"""
        await connection.execute("BEGIN IMMEDIATE")
        async with connection.execute("PRAGMA table_info(deletion_reviews)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        # 较早版本重建通行证表时已使用最新建表定义，避免重复添加快照列。
        for name, limit in (("name", 200), ("abstract", 10000), ("uploader", 200)):
            if name not in columns:
                await connection.execute(
                    f"ALTER TABLE deletion_reviews ADD COLUMN {name} TEXT NULL "
                    f"CHECK({name} IS NULL OR length({name}) BETWEEN 1 AND {limit})"
                )
        for field in ("name", "abstract"):
            await connection.execute(
                f"UPDATE deletion_reviews SET {field}=(SELECT i.{field} FROM ingestion_reviews AS i "
                "WHERE i.resource_id=deletion_reviews.resource_id AND i.status='approved' "
                "AND i.source_platform=deletion_reviews.source_platform "
                "AND i.source_id=deletion_reviews.source_id) "
                f"WHERE {field} IS NULL"
            )
        # 入库申请人不一定是原上传人，不以其他人员字段猜测旧 uploader。
        await connection.execute("PRAGMA user_version=8")
        await connection.commit()

    @staticmethod
    async def _upgrade_passports(connection) -> None:
        """重建旧长度约束为 JSON 数组约束；两表、原索引和自增进度在同一事务中升级。"""
        schema = await asyncio.to_thread(_SCHEMA_PATH.read_text)
        await connection.execute("BEGIN IMMEDIATE")
        for table in ("ingestion_reviews", "deletion_reviews"):
            async with connection.execute(f"SELECT * FROM {table} ORDER BY id") as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            async with connection.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)) as cursor:
                sequence = await cursor.fetchone()
            async with connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)) as cursor:
                indexes = [row["sql"] for row in await cursor.fetchall()]
            for row in rows:
                # 旧列是业务字符串；即使文本恰好长得像 JSON，也必须作为单个通行证保留。
                if row["passport"] is not None:
                    row["passport"] = encode_passports(normalize_passports(row["passport"], legacy_string=True))
            await connection.execute(f"ALTER TABLE {table} RENAME TO {table}_passport_legacy")
            begin = schema.index(f"CREATE TABLE IF NOT EXISTS {table} (")
            end = schema.index("\n);", begin) + len("\n);")
            await connection.execute(schema[begin:end])
            if rows:
                columns = list(rows[0])
                await connection.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [tuple(row[key] for key in columns) for row in rows],
                )
            await connection.execute(f"DROP TABLE {table}_passport_legacy")
            for sql in indexes:
                await connection.execute(sql)
            for line in schema.splitlines():
                if line.startswith("CREATE ") and "INDEX " in line and f" ON {table}(" in line:
                    await connection.execute(line)
            if sequence is not None:
                await connection.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
                await connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (table, sequence["seq"]))
        await connection.execute("PRAGMA user_version=7")
        await connection.commit()

    @asynccontextmanager
    async def connection(self):
        """独占使用受管理连接，避免并行协程的 SQL 混入同一个事务。"""
        await self.initialize()
        async with self._operation_lock:
            if self._closed or self._connection is None:
                raise RuntimeError("SQLite 数据库已关闭")
            connection = self._connection
            try:
                yield connection
            finally:
                # 排空此前已提交给工作线程的操作，取消时也不释放未结束的事务。
                async with connection.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
                if connection.in_transaction:
                    await connection.rollback()

    @asynccontextmanager
    async def transaction(self):
        """事务成功提交，异常或取消回滚；上下文内使用同一个连接。"""
        async with self.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def close(self) -> None:
        async with self._initialize_lock:
            self._closed = True
            async with self._operation_lock:
                if self._connection is not None:
                    await self._connection.close()
                    self._connection = None

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, *args):
        await self.close()
