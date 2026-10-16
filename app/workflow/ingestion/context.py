"""运行依赖保存在 LangGraph context，不进入状态或检查点。"""

import asyncio
from dataclasses import dataclass, field, replace

import httpx
from pydantic import SecretStr
from langgraph.graph.state import CompiledStateGraph

from app.config.ingestion import IngestionSettings
from app.infrastructure.middleware import MiddlewareClient
from app.service.embedding import EmbeddingService
from app.service.middleware_session import MiddlewareSessionService
from app.service.resource_candidates import ReadyResourceSearch
from app.agent.file_deduplication import build_file_deduplication_workflow
from app.agent.file_deduplication.context import FileDeduplicationContext
from app.agent.passport_recommendation import build_passport_recommendation_workflow
from app.agent.passport_recommendation.context import PassportRecommendationContext
from app.service.resource_pdf import ResourcePDFService
from app.service.pending_review import PendingReviewService
from app.repository.resource import MySQLResourceRepository
from app.repository.review import SQLiteReviewRepository


@dataclass
class IngestionWorkflowContext:
    middleware: MiddlewareClient
    sessions: MiddlewareSessionService
    embeddings: EmbeddingService
    candidates: ReadyResourceSearch
    settings: IngestionSettings
    deduplication_workflow: CompiledStateGraph = field(default_factory=build_file_deduplication_workflow, repr=False)
    deduplication_context: FileDeduplicationContext = field(default_factory=FileDeduplicationContext)
    pdf_service: ResourcePDFService | None = None
    passport_workflow: CompiledStateGraph = field(default_factory=build_passport_recommendation_workflow, repr=False)
    passport_context: PassportRecommendationContext = field(default_factory=PassportRecommendationContext)
    pending_review_service: PendingReviewService | None = None
    resource_repository: MySQLResourceRepository | None = None
    reviews: SQLiteReviewRepository | None = None
    request_timeout_seconds: float = 40
    comparison_limit: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self):
        self.comparison_limit = asyncio.Semaphore(self.settings.comparison_concurrency)
        if self.reviews is None and self.pending_review_service is not None:
            self.reviews = self.pending_review_service.reviews
        if self.pending_review_service is not None and self.pending_review_service.files.root != self.settings.ingestion_file_root:
            raise ValueError("待审核落盘服务必须使用 ingestion 暂存目录")
        if self.resource_repository is None:
            if self.pending_review_service is not None:
                self.resource_repository = self.pending_review_service.repository
            elif isinstance(self.candidates, ReadyResourceSearch):
                self.resource_repository = self.candidates.repository
        if self.pdf_service is None:
            self.pdf_service = ResourcePDFService(ingestion_settings=self.settings)
        if self.passport_context.group_search is None:
            # 与判重共用 HNSW 服务及其锁；保留调用方显式提供的搜索和判断配置。
            self.passport_context = replace(
                self.passport_context, resource_search=self.passport_context.resource_search or self.candidates,
            )

    def token(self) -> SecretStr:
        token = self.sessions.get_access_token()
        if token is None:
            raise RuntimeError("中间件会话尚未就绪")
        return token

    def invalidate_on_unauthorized(self, error: Exception, token: SecretStr) -> None:
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 401:
            self.sessions.invalidate_access_token(token)
