"""MySQL 资源持久化，资源 ID 关联审核记录，平台与附件名支持历史资源查询。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone

import aiomysql

from app.config.database import MySQLSettings, load_mysql_settings
from app.model.resource import StoredFile, StoredResource
from app.model.deletion import DeletionResource
from app.model.passport import encode_passports, decode_passports, passport_record, same_passports


class ResourceApprovalConflict(ValueError):
    """正式文件身份已经由其他入库申请占用，不能覆盖。"""


class ResourceDeletionConflict(ValueError):
    """删除审核绑定的正式资源身份发生变化，不能删除其他文件。"""


class MySQLResourceRepository:
    def __init__(self, settings: MySQLSettings | None = None):
        self._settings = settings
        self._pool: aiomysql.Pool | None = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._pool is not None:
                return
            # 仅使用查询接口时也能独立读取配置，不强制启用中间件消费。
            settings = self._settings or load_mysql_settings()
            self._settings = settings
            pool = None
            try:
                async with asyncio.timeout(settings.operation_timeout_seconds):
                    pool = await aiomysql.create_pool(
                        host=settings.host, port=settings.port, user=settings.user,
                        password=settings.password.get_secret_value(), db=settings.database,
                        charset="utf8mb4", autocommit=True, minsize=1, maxsize=5,
                        connect_timeout=settings.connect_timeout_seconds,
                        pool_recycle=300, init_command="SET time_zone = '+00:00'",
                    )
                self._pool = pool
            except BaseException:
                if pool is not None:
                    pool.close()
                    await pool.wait_closed()
                raise

    @staticmethod
    def _stored(row) -> StoredResource:
        # 文件标识与入库消息 ID 相同（消息契约校验）；DTO 保留运行时标识，不存储消息列。
        return StoredResource(row["file_url"].rsplit(".", 1)[0], row["source_platform"], row["source_id"],
                              StoredFile(row["file_url"]), row["id"])

    async def find(self, source_platform: str, file_url: str, *, resource_id: int | None = None) -> StoredResource | None:
        if resource_id is not None:
            return await self.find_by_id(resource_id)
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                # 跨库提交间隙用唯一平台附件键恢复。
                await cursor.execute(
                    "SELECT id,source_platform,source_id,file_url FROM resources "
                    "WHERE source_platform=%s AND file_url=%s",
                    (source_platform, file_url),
                )
                row = await cursor.fetchone()
        return self._stored(row) if row is not None else None

    async def find_by_id(self, resource_id: int) -> StoredResource | None:
        """按注册中心正式资源主键定位附件，不使用源平台的 source_id。"""
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT id,source_platform,source_id,file_url FROM resources WHERE id=%s",
                    (resource_id,),
                )
                row = await cursor.fetchone()
        return self._stored(row) if row is not None else None

    @property
    def pool(self) -> aiomysql.Pool:
        if self._pool is None:
            raise RuntimeError("资源仓库尚未初始化")
        return self._pool

    async def find_active_source(self, source_platform: str, source_id: str | None, *, exclude_file_url: str) -> StoredResource | None:
        if source_id is None:
            return None
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT id,source_platform,source_id,file_url FROM resources "
                    "WHERE source_platform=%s AND source_id=%s AND file_url <> %s "
                    "ORDER BY id LIMIT 1",
                    (source_platform,source_id,exclude_file_url),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        return self._stored(row)

    async def list_vectors(self, source_platform: str | None = None) -> list[dict]:
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                # 未指定平台时供通行证推荐使用；正式资源表不需要审核状态条件。
                platform_condition = "source_platform=%s AND " if source_platform is not None else ""
                await cursor.execute(
                    "SELECT id,source_platform,source_id,file_url,name,abstract,applicant,reviewer,passport,embedding FROM resources "
                    "WHERE " + platform_condition + "embedding IS NOT NULL ORDER BY id",
                    (source_platform,) if source_platform is not None else (),
                )
                rows = await cursor.fetchall()
        return [{**passport_record(row), "embedding": json.loads(row["embedding"])} for row in rows]

    async def list_basic_info(self) -> list[dict]:
        """读取全部正式资源基本信息、入库文件名称及最终通行证，不加载向量、路径或审核备注。"""
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT id,source_platform,name,applicant,reviewer,created_at AS ingested_at,abstract,passport "
                    "FROM resources ORDER BY id",
                )
                rows = await cursor.fetchall()
        # MySQL DATETIME 不携带时区；本项目按 UTC 存储，连接也固定为 UTC。
        return [{**passport_record(row), "ingested_at": row["ingested_at"].replace(tzinfo=timezone.utc)} for row in rows]

    @asynccontextmanager
    async def approve_ingestion(self, review: dict):
        """在文件完整晋升后提交正式资源；重试只复用全部入库快照一致的记录。"""
        fields = ("source_platform", "source_id", "file_url", "name", "abstract", "applicant", "applicant_note",
                  "reviewer", "reviewer_note", "requested_at", "embedding", "passport")
        values = {key: review[key] for key in fields}
        values["requested_at"] = datetime.fromisoformat(review["requested_at"]).astimezone(timezone.utc).replace(tzinfo=None)
        vector = json.loads(values["embedding"]) if values["embedding"] is not None else None
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor(aiomysql.DictCursor) as cursor:
                        # 锁定文件与源身份；禁止覆盖其他申请，并允许提交成功后 SQLite 尚未回填的重试。
                        await cursor.execute(
                            "SELECT id," + ",".join(fields) + " FROM resources WHERE source_platform=%s "
                            "AND (file_url=%s OR (source_id IS NOT NULL AND source_id=%s)) FOR UPDATE",
                            (review["source_platform"], review["file_url"], review["source_id"]),
                        )
                        rows = await cursor.fetchall()
                        if rows:
                            actual = rows[0]
                            same = len(rows) == 1 and all(actual[key] == values[key] for key in fields if key not in ("embedding", "passport"))
                            same = same and decode_passports(actual["passport"]) == values["passport"]
                            same = same and (json.loads(actual["embedding"]) if actual["embedding"] is not None else None) == vector
                            if not same:
                                raise ResourceApprovalConflict("同平台文件或源 ID 已对应其他正式资源")
                            resource_id = actual["id"]
                        else:
                            await cursor.execute(
                                "INSERT INTO resources (" + ",".join(fields) + ") VALUES (" + ",".join(["%s"] * len(fields)) + ")",
                                tuple(encode_passports(values[key]) if key == "passport" else values[key] for key in fields),
                            )
                            resource_id = cursor.lastrowid
                        yield resource_id
                    # 文件晋升在事务体内执行，正式资源行仅在原件发布完成后对其他查询可见。
                    await connection.commit()
                except BaseException:
                    await asyncio.shield(connection.rollback())
                    raise

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    @asynccontextmanager
    async def delete_for_review(self, review: dict):
        """只删除审核绑定的正式资源，原件保留完成后提交；不存在的旧目标交给恢复流程核对。"""
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor(aiomysql.DictCursor) as cursor:
                        await cursor.execute("SELECT id,source_platform,source_id,file_url,passport FROM resources WHERE id=%s FOR UPDATE",
                                             (int(review["resource_id"]),))
                        row = await cursor.fetchone()
                        if row is not None:
                            keys = ("source_platform", "source_id", "file_url")
                            if (any(row[key] != review[key] for key in keys)
                                    or not same_passports(decode_passports(row["passport"]), review["passport"])):
                                raise ResourceDeletionConflict("待删除正式资源的身份或通行证已变化")
                            await cursor.execute("DELETE FROM resources WHERE id=%s", (row["id"],))
                        yield row is not None
                    await connection.commit()
                except BaseException:
                    await asyncio.shield(connection.rollback())
                    raise

    async def find_deletion_source(self, source_platform: str, source_id: str) -> DeletionResource | None:
        """按平台与源 ID 精确定位正式资源；多个匹配不能任选一个送审。"""
        await self.initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self.pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT id,source_platform,source_id,file_url,passport FROM resources "
                    "WHERE source_platform=%s AND source_id=%s ORDER BY id LIMIT 2",
                    (source_platform, source_id),
                )
                rows = await cursor.fetchall()
        if len(rows) > 1:
            raise ValueError("同平台源 ID 对应多个正式资源，无法安全定位删除目标")
        if not rows:
            return None
        row = rows[0]
        row = passport_record(row)
        return DeletionResource(resource_id=row["id"], **{key: row[key] for key in
            ("source_platform", "source_id", "file_url", "passport")})
