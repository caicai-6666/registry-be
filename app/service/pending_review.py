"""原文件持久化后提交完整待审核记录，不在此服务中 ACK。"""

from app.agent.passport_recommendation import PassportRecommendationInput, PassportRecommendationResult
from app.infrastructure.resource_store import ResourceFileStore
from app.model.resource import ActiveSourceConflict, IngestionDelivery, StoredFile
from app.repository.resource import MySQLResourceRepository
from app.repository.review import SQLiteReviewRepository


class PendingReviewService:
    def __init__(self, repository: MySQLResourceRepository, files: ResourceFileStore, *, max_file_bytes: int,
                 reviews: SQLiteReviewRepository):
        self.repository = repository
        self.files = files
        self.max_file_bytes = max_file_bytes
        self.reviews = reviews

    async def persist(self, delivery: IngestionDelivery, content: bytes, *, embedding: list[float], recommendations: list) -> int:
        message = delivery.message
        review = await self.reviews.find(delivery)
        if review is not None and review["status"] == "rejected":
            raise ValueError("已拒绝的申请不能保存为待审核资源")
        # 所有业务输入先验证，再产生文件或数据库副作用；None 不等于明确无推荐。
        validated = PassportRecommendationInput(name=message.name, abstract=message.abstract, abstract_embedding=embedding)
        result = PassportRecommendationResult(recommendations=recommendations)
        if not isinstance(content, bytes) or not 0 < len(content) <= self.max_file_bytes:
            raise ValueError("待审核原文件为空或超过文件大小上限")
        file = StoredFile(message.file_url)
        active_review = await self.reviews.find_pending_source(
            message.platform_code,message.source_id,exclude_message_id=message.message_id,
        )
        if active_review is not None:
            raise ActiveSourceConflict(self.reviews.pending_resource(active_review))
        # 只读查询正式资源用于身份拦截；当前申请永远不写入 MySQL。
        active = await self.repository.find_active_source(message.platform_code,message.source_id,exclude_file_url=message.file_url)
        if active is not None:
            raise ActiveSourceConflict(active)
        if review is None or review["status"] == "pending":
            if not await self.files.exists(message.platform_code, file):
                async def chunks():
                    for start in range(0, len(content), 1024 * 1024):
                        yield content[start:start + 1024 * 1024]
                try:
                    await self.files.save(message, chunks(), expected_size=len(content), overwrite=False)
                except FileExistsError:
                    # 另一个相同消息的处理者已发布文件，下面校验内容后再复用。
                    pass
            saved = await self.files.read(message.platform_code, file.file_url, max_file_bytes=self.max_file_bytes)
            if saved != content:
                raise ValueError("同名已保存附件与当前申请内容不一致，不能覆盖")
        return await self.reviews.save_pending(
            delivery, embedding=validated.abstract_embedding, recommendations=result.recommendations,
        )
