"""删除审核记录：文件描述快照、正式资源引用、人工决定与反馈凭据。"""

from datetime import datetime, timezone

from app.infrastructure.sqlite import SQLiteDatabase
from app.model.passport import encode_passports, decode_passports, passport_record, same_passports
from app.model.deletion import DeletionDelivery, DeletionResource
from app.model.review import ReviewConflict, ReviewNotFound
from app.repository.review import AUTO_REVIEWER, utc_text


class DeletionReviewConflict(ValueError):
    """正式资源已绑定另一条删除审核记录，不覆盖既有申请。"""


class SQLiteDeletionReviewRepository:
    def __init__(self, database: SQLiteDatabase):
        self.database = database

    async def get_by_id(self, review_id: int) -> dict | None:
        async with self.database.connection() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
        return passport_record(row) if row is not None else None

    async def list_pending(self) -> list[dict]:
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT id,resource_id,source_platform,source_id,passport,name,abstract,uploader,applicant,applicant_note,requested_at "
                "FROM deletion_reviews WHERE status='pending' AND decision IS NULL ORDER BY id",
            ) as cursor:
                return [{**passport_record(row), "resource_id": int(row["resource_id"]) if row["resource_id"] else None}
                        for row in await cursor.fetchall()]

    async def list_history(self, reviewer: str) -> list[dict]:
        # 审核历史保留原资源 ID，删除 MySQL 行后仍能由已批准的入库记录找到原审核人。
        # 使用 EXISTS，两个身份同时命中时也只返回一次；不按申请人或可复用的源 ID 扩大范围。
        async with self.database.connection() as connection:
            async with connection.execute(
                "SELECT d.id,d.resource_id,d.source_platform,d.source_id,d.passport,d.name,d.abstract,d.uploader,"
                "d.applicant,d.applicant_note,d.requested_at,"
                "d.status,d.reviewer,d.reviewer_note,d.reviewed_at,d.result_message_id FROM deletion_reviews AS d "
                "WHERE d.status IN ('approved','rejected') AND (d.reviewer=? OR EXISTS ("
                "SELECT 1 FROM ingestion_reviews AS i WHERE i.resource_id=d.resource_id "
                "AND i.status='approved' AND i.reviewer=?)) ORDER BY d.reviewed_at DESC,d.id DESC", (reviewer, reviewer),
            ) as cursor:
                return [{**passport_record(row), "resource_id": int(row["resource_id"]) if row["resource_id"] else None}
                        for row in await cursor.fetchall()]

    async def reserve_decision(self, review_id: int, *, decision: str, reviewer: str, note: str) -> dict:
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ReviewNotFound("删除审核申请不存在")
            if row["decision"] is not None:
                if (row["decision"], row["reviewer"], row["reviewer_note"]) != (decision, reviewer, note):
                    raise ReviewConflict("删除审核决定已确定，不能更改")
                return passport_record(row)
            if row["status"] != "pending":
                raise ReviewConflict("该删除申请已经完成审核")
            if row["resource_id"] is None:
                raise ReviewConflict("删除申请缺少正式资源关联")
            now = utc_text(datetime.now(timezone.utc))
            await connection.execute(
                "UPDATE deletion_reviews SET decision=?,reviewer=?,reviewer_note=?,reviewed_at=?,updated_at=? WHERE id=?",
                (decision, reviewer, note, now, now, review_id),
            )
            return {**passport_record(row), "decision": decision, "reviewer": reviewer, "reviewer_note": note, "reviewed_at": now}

    async def complete_decision(self, review_id: int) -> dict:
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["decision"] is None or row["status"] not in ("pending", row["decision"]):
                raise ReviewConflict("删除审核决定尚未确定或状态冲突")
            await connection.execute("UPDATE deletion_reviews SET status=decision,updated_at=? WHERE id=?",
                                     (utc_text(datetime.now(timezone.utc)), review_id))
        return await self.get_by_id(review_id)

    async def release_conflicting_approval(self, row: dict) -> None:
        # 正式身份在事务进入文件移动之前冲突，尚未删除任何数据，可恢复为待审。
        async with self.database.transaction() as connection:
            await connection.execute(
                "UPDATE deletion_reviews SET decision=NULL,reviewer=NULL,reviewer_note=NULL,reviewed_at=NULL,updated_at=? "
                "WHERE id=? AND status='pending' AND decision='approved' AND reviewer=? AND reviewer_note=?",
                (utc_text(datetime.now(timezone.utc)), row["id"], row["reviewer"], row["reviewer_note"]),
            )

    async def mark_decision_published(self, review_id: int, result_message_id: str) -> None:
        if not isinstance(result_message_id, str) or not result_message_id.strip() or len(result_message_id) > 128:
            raise ValueError("删除审核反馈必须返回有效消息 ID")
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE id=?", (review_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["decision"] is None or row["status"] != row["decision"]:
                raise ReviewConflict("删除审核本地处理尚未完成")
            if row["result_message_id"] is not None and row["result_message_id"] != result_message_id:
                raise ReviewConflict("删除审核反馈消息 ID 冲突")
            await connection.execute("UPDATE deletion_reviews SET result_message_id=?,updated_at=? WHERE id=?",
                                     (result_message_id, utc_text(datetime.now(timezone.utc)), review_id))

    async def list_unfinished_decisions(self) -> list[int]:
        async with self.database.connection() as connection:
            async with connection.execute("SELECT id FROM deletion_reviews WHERE decision IS NOT NULL AND result_message_id IS NULL ORDER BY id") as cursor:
                return [row["id"] for row in await cursor.fetchall()]

    @staticmethod
    def _validate_delivery(row, delivery: DeletionDelivery) -> None:
        message = delivery.message
        expected = (str(delivery.offset), message.platform_code, message.source_id, message.passport,
                    message.applicant, message.note, utc_text(message.created_at))
        keys = ("request_offset", "source_platform", "source_id", "passport", "applicant", "applicant_note", "requested_at")
        actual = tuple(decode_passports(row[key]) if key == "passport" else row[key] for key in keys)
        if actual != expected:
            raise ValueError("重复删除消息与已有审核申请不一致")

    async def find(self, delivery: DeletionDelivery) -> dict | None:
        async with self.database.connection() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE message_id=?",
                                          (delivery.message.message_id,)) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        self._validate_delivery(row, delivery)
        return passport_record(row)

    async def save_pending(self, delivery: DeletionDelivery, resource: DeletionResource) -> int:
        message = delivery.message
        if ((resource.source_platform, resource.source_id) != (message.platform_code, message.source_id)
                or not same_passports(resource.passport, message.passport)):
            raise ValueError("删除申请与正式资源身份或通行证不一致")
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE message_id=?",
                                          (message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                self._validate_delivery(row, delivery)
                if row["status"] == "rejected":
                    raise ValueError("已拒绝删除申请不能重新进入待审核状态")
                if (row["resource_id"], row["file_url"]) != (str(resource.resource_id), resource.file_url):
                    raise ValueError("已有删除申请的正式文件引用不一致")
                return row["id"]
            # 保留既有一一对应约束；冲突由消费者形成独立的无绑定拒绝记录。
            async with connection.execute("SELECT 1 FROM deletion_reviews WHERE resource_id=?",
                                          (str(resource.resource_id),)) as cursor:
                if await cursor.fetchone() is not None:
                    raise DeletionReviewConflict("该资源已有删除审核记录")
            # 业务描述取申请消息的首次快照，不作为路径或资源身份，也不随重投覆盖。
            async with connection.execute(
                "INSERT INTO deletion_reviews (message_id,resource_id,request_offset,source_platform,source_id,"
                "passport,file_url,name,abstract,uploader,applicant,applicant_note,requested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (message.message_id, str(resource.resource_id), str(delivery.offset), message.platform_code,
                 message.source_id, encode_passports(message.passport), resource.file_url,
                 message.name, message.abstract, message.uploader, message.applicant, message.note,
                 utc_text(message.created_at)),
            ) as cursor:
                return cursor.lastrowid

    async def save_rejected(self, delivery: DeletionDelivery, note: str) -> None:
        if not isinstance(note, str) or not note.strip() or len(note) > 10000:
            raise ValueError("删除拒绝意见必须有效且最多 10000 字符")
        message = delivery.message
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE message_id=?",
                                          (message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is not None:
                self._validate_delivery(row, delivery)
                if row["status"] != "rejected" or row["reviewer_note"] != note:
                    raise ValueError("删除拒绝结论与已有审核记录冲突")
                return
            # 不存在、通行证不符或绑定冲突均不占用资源唯一关联，也不猜测文件路径。
            await connection.execute(
                "INSERT INTO deletion_reviews (message_id,request_offset,source_platform,source_id,passport,"
                "name,abstract,uploader,applicant,applicant_note,reviewer,reviewer_note,status,requested_at,reviewed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (message.message_id, str(delivery.offset), message.platform_code, message.source_id, encode_passports(message.passport),
                 message.name, message.abstract, message.uploader, message.applicant, message.note,
                 AUTO_REVIEWER, note, "rejected", utc_text(message.created_at),
                 utc_text(datetime.now(timezone.utc))),
            )

    async def mark_rejection_published(self, delivery: DeletionDelivery, result_message_id: str) -> None:
        if not isinstance(result_message_id, str) or not result_message_id.strip() or len(result_message_id) > 128:
            raise ValueError("删除拒绝发布必须返回有效消息 ID")
        async with self.database.transaction() as connection:
            async with connection.execute("SELECT * FROM deletion_reviews WHERE message_id=?",
                                          (delivery.message.message_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None or row["status"] != "rejected":
                raise ValueError("删除拒绝审核尚未保存")
            self._validate_delivery(row, delivery)
            if row["result_message_id"] is not None:
                if row["result_message_id"] != result_message_id:
                    raise ValueError("删除拒绝反馈消息 ID 冲突")
                return
            await connection.execute("UPDATE deletion_reviews SET result_message_id=?,updated_at=? WHERE id=?",
                (result_message_id, utc_text(datetime.now(timezone.utc)), row["id"]))
