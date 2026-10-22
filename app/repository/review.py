"""SQLite 暂存完整入库申请及审核结论；待审核阶段不创建 MySQL 资源。"""

import json
from datetime import datetime, timezone

from app.agent.passport_recommendation import PassportRecommendationInput, PassportRecommendationResult
from app.infrastructure.sqlite import SQLiteDatabase
from app.model.file_level import FILE_LEVEL
from app.model.passport import encode_passports, decode_passports, passport_record
from app.model.resource import ActiveSourceConflict, IngestionDelivery, StoredFile, StoredResource

AUTO_REVIEWER = "注册中心自动审核员"


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SQLiteReviewRepository:
    def __init__(self, database: SQLiteDatabase):
        self.database = database

    @staticmethod
    def _validate_delivery(row, delivery: IngestionDelivery) -> None:
        message = delivery.message
        expected = (message.platform_code, message.source_id, message.applicant,
                    message.reviewer, message.note, utc_text(message.created_at))
        # 旧实现未将来源提交人写入 applicant；缺失时用已保存的上游 reviewer 核对，不影响消息重投。
        actual = (row["source_platform"], row["source_id"],
                  row["applicant"] if row["applicant"] is not None else row["source_reviewer"],
                  row["source_reviewer"], row["applicant_note"], row["requested_at"])
        if actual != expected:
            raise ValueError("重复消息与已有审核申请不一致")
        # 旧审核表可能尚无文件快照；已有快照必须逐项一致。
        if row["file_url"] is not None and (row["file_url"],row["name"],row["abstract"]) != (
            message.file_url,message.name,message.abstract,
        ):
            raise ValueError("重复消息与已有文件元信息不一致")

    @staticmethod
    def pending_resource(row) -> StoredResource:
        return StoredResource(row["message_id"], row["source_platform"], row["source_id"],
                              StoredFile(row["file_url"] or ""))

    async def find(self, delivery: IngestionDelivery) -> dict | None:
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT * FROM ingestion_reviews WHERE message_id=?", (delivery.message.message_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        self._validate_delivery(row, delivery)
        return passport_record(row)

    async def find_pending_source(self, source_platform: str, source_id: str | None, *, exclude_message_id: str) -> dict | None:
        if source_id is None:
            return None
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT * FROM ingestion_reviews WHERE source_platform=? AND source_id=? AND status='pending' AND message_id<>? LIMIT 1",
                (source_platform, source_id, exclude_message_id),
            ) as cursor:
                row = await cursor.fetchone()
        return passport_record(row) if row is not None else None

    async def save_pending(self, delivery: IngestionDelivery, *, embedding: list[float] | None = None,
                           recommendations: list | None = None) -> int:
        message = delivery.message
        vector = None
        recommended = None
        if embedding is not None:
            validated = PassportRecommendationInput(name=message.name,abstract=message.abstract,abstract_embedding=embedding)
            vector = json.dumps(validated.abstract_embedding,allow_nan=False)
        if recommendations is not None:
            result = PassportRecommendationResult(recommendations=recommendations)
            recommended = json.dumps([item.model_dump() for item in result.recommendations],ensure_ascii=False)
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE message_id=?", (message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                self._validate_delivery(row, delivery)
                if row["status"] == "rejected":
                    raise ValueError("已拒绝申请不能重新进入待审核状态")
                # 重投仅补齐 pending 的缺失处理结果，不覆盖人工结论和既有推荐。
                if row["status"] == "pending" and row["decision"] is None:
                    await connection.execute(
                        "UPDATE ingestion_reviews SET file_url=COALESCE(file_url,?),name=COALESCE(name,?),"
                        "abstract=COALESCE(abstract,?),embedding=COALESCE(embedding,?),"
                        "passport_recommendations=COALESCE(passport_recommendations,?),applicant=COALESCE(applicant,?),updated_at=? WHERE id=?",
                        (message.file_url,message.name,message.abstract,vector,recommended,message.applicant,
                         utc_text(datetime.now(timezone.utc)),row["id"]),
                    )
                return row["id"]
            if message.source_id is not None:
                async with connection.execute(
                    "SELECT * FROM ingestion_reviews WHERE source_platform=? AND source_id=? AND status='pending' LIMIT 1",
                    (message.platform_code,message.source_id),
                ) as cursor:
                    active = await cursor.fetchone()
                if active is not None:
                    raise ActiveSourceConflict(self.pending_resource(active))
            async with connection.execute(
                "INSERT INTO ingestion_reviews (message_id,request_offset,source_platform,source_id,file_url,name,abstract,"
                "embedding,passport_recommendations,applicant,source_reviewer,applicant_note,requested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (message.message_id,str(delivery.offset),message.platform_code,message.source_id,message.file_url,
                 message.name,message.abstract,vector,recommended,message.applicant,message.reviewer,message.note,
                 utc_text(message.created_at)),
            ) as cursor:
                return cursor.lastrowid

    async def save_rejected(self, delivery: IngestionDelivery, note: str) -> None:
        if not isinstance(note,str) or not note.strip() or len(note)>10000:
            raise ValueError("拒绝审核必须提供有效理由")
        message = delivery.message
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE message_id=?", (message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                self._validate_delivery(row, delivery)
                if row["status"] != "rejected" or row["reviewer_note"] != note:
                    raise ValueError("拒绝结论与已有审核申请冲突")
                return
            await connection.execute(
                "INSERT INTO ingestion_reviews (message_id,request_offset,source_platform,source_id,file_url,name,abstract,"
                "applicant,source_reviewer,applicant_note,reviewer,reviewer_note,status,requested_at,reviewed_at,passport) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (message.message_id,str(delivery.offset),message.platform_code,message.source_id,message.file_url,
                 message.name,message.abstract,message.applicant,message.reviewer,message.note,AUTO_REVIEWER,note,
                 "rejected",utc_text(message.created_at),utc_text(datetime.now(timezone.utc)),encode_passports([])),
            )

    async def mark_rejection_published(self, delivery: IngestionDelivery, result_message_id: str) -> None:
        if not isinstance(result_message_id,str) or not 0<len(result_message_id)<=128:
            raise ValueError("拒绝反馈必须返回有效消息 ID")
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE message_id=?", (delivery.message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["status"] != "rejected":
                raise ValueError("拒绝审核尚未保存")
            self._validate_delivery(row, delivery)
            if row["result_message_id"] is not None:
                if row["result_message_id"] != result_message_id:
                    raise ValueError("拒绝反馈消息 ID 冲突")
                return
            await connection.execute(
                "UPDATE ingestion_reviews SET result_message_id=?,updated_at=? WHERE message_id=?",
                (result_message_id,utc_text(datetime.now(timezone.utc)),delivery.message.message_id),
            )

    async def get_by_id(self, review_id: int) -> dict | None:
        async with self.database.connection() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
        return passport_record(row) if row is not None else None

    async def list_pending(self) -> list[dict]:
        # 已确定决定但尚在处理的申请不再交给另一个审核人；不加载向量或磁盘路径。
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT id,source_platform,source_id,name,abstract,COALESCE(applicant,source_reviewer) AS applicant,applicant_note,"
                "requested_at,passport_recommendations FROM ingestion_reviews "
                "WHERE status='pending' AND decision IS NULL ORDER BY id",
            ) as cursor:
                rows = await cursor.fetchall()
        return [{**dict(row), "passport_recommendations": json.loads(row["passport_recommendations"])
                 if row["passport_recommendations"] is not None else None} for row in rows]

    async def list_history(self, reviewer: str) -> list[dict]:
        """按最终审核人精确查询本地已完成记录，不以来源平台审核人或反馈是否发布过滤。"""
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT id,source_platform,source_id,name,abstract,COALESCE(applicant,source_reviewer) AS applicant,applicant_note,"
                "requested_at,passport_recommendations,resource_id,status,passport,file_level,reviewer,reviewer_note,"
                "reviewed_at,result_message_id FROM ingestion_reviews "
                "WHERE reviewer=? AND status IN ('approved','rejected') ORDER BY reviewed_at DESC,id DESC",
                (reviewer,),
            ) as cursor:
                rows = await cursor.fetchall()
        # 审核历史独立于当前正式资源是否存在；不从 MySQL 补全旧记录或加载摘要向量。
        return [{**passport_record(row), "resource_id": int(row["resource_id"]) if row["resource_id"] is not None else None,
                 "passport_recommendations": json.loads(row["passport_recommendations"])
                 if row["passport_recommendations"] is not None else None} for row in rows]

    async def reserve_decision(self, review_id: int, *, decision: str, passport: list[str], reviewer: str, note: str, file_level: int | None = None) -> dict:
        """以 SQLite 写事务确定唯一人工决定，等级与通行证一起冻结，恢复时不能改写。"""
        if decision == 'approved':
            FILE_LEVEL.validate_python(file_level)
        elif file_level is not None:
            raise ValueError('拒绝入库不能设置文件等级')
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise LookupError("入库审核申请不存在")
            if row["decision"] is not None:
                if (row["decision"], decode_passports(row["passport"]), row["reviewer"], row["reviewer_note"], row["file_level"]) != (decision, passport, reviewer, note, file_level):
                    raise ValueError("入库审核决定已确定，不能更改")
                return passport_record(row)
            if row["status"] != "pending":
                raise ValueError("该申请已经完成审核")
            now = utc_text(datetime.now(timezone.utc))
            await connection.execute(
                "UPDATE ingestion_reviews SET decision=?,passport=?,file_level=?,reviewer=?,reviewer_note=?,reviewed_at=?,updated_at=?,"
                "applicant=COALESCE(applicant,source_reviewer) WHERE id=?",
                (decision, encode_passports(passport), file_level, reviewer, note, now, now, review_id),
            )
            return {**dict(row), "applicant": row["applicant"] if row["applicant"] is not None else row["source_reviewer"],
                    "decision": decision, "passport": passport, "file_level": file_level, "reviewer": reviewer,
                    "reviewer_note": note, "reviewed_at": now, "updated_at": now}

    async def complete_decision(self, review_id: int, *, resource_id: int | None) -> dict:
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["decision"] is None:
                raise ValueError("人工审核决定尚未确定")
            if (row["decision"] == "approved") != (resource_id is not None):
                raise ValueError("审核结果与正式资源关联不一致")
            identity = str(resource_id) if resource_id is not None else None
            if row["status"] != "pending" and (row["status"], row["resource_id"]) != (row["decision"], identity):
                raise ValueError("审核记录的正式资源关联冲突")
            await connection.execute(
                "UPDATE ingestion_reviews SET status=decision,resource_id=?,updated_at=? WHERE id=?",
                (identity, utc_text(datetime.now(timezone.utc)), review_id),
            )
        return await self.get_by_id(review_id)

    async def release_conflicting_approval(self, review: dict) -> None:
        """正式身份校验失败且未执行文件晋升时释放预留，让审核人仍可拒绝该申请。"""
        async with self.database.transaction() as connection:
            await connection.execute(
                "UPDATE ingestion_reviews SET decision=NULL,passport=NULL,file_level=NULL,reviewer=NULL,reviewer_note=NULL,"
                "reviewed_at=NULL,updated_at=? WHERE id=? AND status='pending' AND resource_id IS NULL "
                "AND decision='approved' AND passport=? AND reviewer=? AND reviewer_note=? AND file_level IS ?",
                (utc_text(datetime.now(timezone.utc)), review["id"], encode_passports(review["passport"]), review["reviewer"], review["reviewer_note"], review["file_level"]),
            )

    async def mark_decision_published(self, review_id: int, result_message_id: str) -> None:
        if not isinstance(result_message_id, str) or not 0 < len(result_message_id) <= 128:
            raise ValueError("审核反馈必须返回有效消息 ID")
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM ingestion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["decision"] is None or row["status"] != row["decision"]:
                raise ValueError("人工审核处理尚未完成")
            if row["result_message_id"] is not None and row["result_message_id"] != result_message_id:
                raise ValueError("审核结果消息 ID 冲突")
            await connection.execute("UPDATE ingestion_reviews SET result_message_id=?,updated_at=? WHERE id=?",
                (result_message_id, utc_text(datetime.now(timezone.utc)), review_id))

    async def list_unfinished_decisions(self) -> list[int]:
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT id FROM ingestion_reviews WHERE decision IS NOT NULL AND result_message_id IS NULL ORDER BY id",
            ) as cursor:
                return [row["id"] for row in await cursor.fetchall()]
