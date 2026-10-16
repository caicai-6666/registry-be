"""知识沉淀注册中心后端的 ASGI 应用入口与服务生命周期。"""

# 直接运行文件时将项目根目录加入导入路径，支持 IDE 和不同工作目录。
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config.auth import AuthSettings, load_auth_settings
from app.user import load_reviewer_user_catalog
from app.service.auth import AuthService, LoginCodeCache
from app.router.auth import router as auth_router
from app.router.platform import router as platform_router
from app.router.catalog import router as catalog_router
from app.router.resource import router as resource_router
from app.router.ingestion_review import router as ingestion_review_router
from app.router.deletion_review import router as deletion_review_router

from app.config.middleware import MiddlewareSettings, load_middleware_settings
from app.config.database import load_mysql_settings
from app.config.deletion import DeletionSettings, load_deletion_settings
from app.config.embedding import load_embedding_settings
from app.config.file_deduplication import load_file_deduplication_settings
from app.config.mllm import load_mllm_settings
from app.config.passport_recommendation import load_passport_recommendation_settings
from app.config.pdf import load_pdf_render_settings
from app.config.platform_directory import PlatformDirectorySettings, load_platform_directory_settings
from app.config.ingestion import IngestionSettings, load_ingestion_settings
from app.config.sqlite import SQLiteSettings, load_sqlite_settings
from app.infrastructure.sqlite import SQLiteDatabase
from app.infrastructure.middleware import MiddlewareClient
from app.infrastructure.mllm import MLLMClient
from app.agent.file_deduplication.context import FileDeduplicationContext
from app.agent.passport_recommendation.context import PassportRecommendationContext
from app.repository.resource import MySQLResourceRepository
from app.repository.review import SQLiteReviewRepository
from app.repository.deletion_review import SQLiteDeletionReviewRepository
from app.service.deletion_consumer import DeletionConsumerService
from app.service.ingestion_consumer import IngestionConsumerService
from app.service.embedding import EmbeddingService
from app.service.pending_review import PendingReviewService
from app.service.resource_candidates import ReadyResourceSearch
from app.service.resource_pdf import ResourcePDFService
from app.service.middleware_session import MiddlewareSessionService
from app.service.platform_directory import PlatformDirectoryService
from app.service.review_files import ReviewFileService
from app.service.ingestion_review import IngestionReviewService
from app.service.deletion_review import DeletionReviewService
from app.workflow.ingestion.context import IngestionWorkflowContext


def create_app(
    settings: MiddlewareSettings | None = None,
    *, middleware_client: MiddlewareClient | None = None,
    ingestion_settings: IngestionSettings | None = None,
    sqlite_settings: SQLiteSettings | None = None,
    auth_settings: AuthSettings | None = None,
    platform_directory_settings: PlatformDirectorySettings | None = None,
    deletion_settings: DeletionSettings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 人员配置必须完整有效；在数据库初始化和中间件连接前先验证。
        auth = auth_settings or load_auth_settings()
        catalog = load_reviewer_user_catalog(auth.reviewer_user_file)
        application.state.reviewer_user_catalog = catalog
        application.state.auth_service = AuthService(
            reviewer_users=catalog,
            login_code_cache=LoginCodeCache(ttl_seconds=auth.login_code_ttl_seconds),
        )
        middleware = settings or load_middleware_settings()
        application.state.middleware_platform_code = middleware.platform_code
        service = MiddlewareSessionService(middleware, client=middleware_client)
        application.state.middleware_session_service = service
        application.state.sqlite_database = None
        application.state.ingestion_consumer_service = None
        application.state.deletion_consumer_service = None
        application.state.ingestion_workflow_context = None
        application.state.review_file_service = None
        application.state.platform_directory_service = None
        application.state.resource_repository = None
        application.state.ingestion_review_service = None
        application.state.deletion_review_service = None
        # 后注册的依赖先关闭；启动中途失败也按同样顺序清理已创建的资源。
        async with AsyncExitStack() as resources:
            sqlite = SQLiteDatabase(sqlite_settings or load_sqlite_settings())
            resources.push_async_callback(sqlite.close)
            resources.push_async_callback(service.close)
            application.state.sqlite_database = sqlite
            await sqlite.initialize()
            ingestion = ingestion_settings or load_ingestion_settings()
            deletion = deletion_settings or load_deletion_settings()
            files = ReviewFileService(ingestion)
            application.state.review_file_service = files
            await files.initialize()
            await service.start()
            directory = PlatformDirectoryService(
                platform_directory_settings or load_platform_directory_settings(), service, middleware=middleware,
            )
            application.state.platform_directory_service = directory
            resources.push_async_callback(directory.close)
            await directory.start()
            # 消费和人工审核共用认证、HTTP 连接与正式资源仓库；只有最终批准流程写入 MySQL。
            database = None
            client = None
            if middleware.enabled and (ingestion.enabled or deletion.enabled):
                database = load_mysql_settings()
            if middleware.enabled:
                client = MiddlewareClient(str(middleware.base_url), timeout_seconds=middleware.request_timeout_seconds)
                resources.push_async_callback(client.close)
            # 正式文件查询和原件读取不依赖中间件；未启用消费时首次查询才连接 MySQL。
            repository = MySQLResourceRepository(database)
            resources.push_async_callback(repository.close)
            application.state.resource_repository = repository
            reviews = SQLiteReviewRepository(sqlite)
            review_service = IngestionReviewService(reviews, repository, files, service, client,
                timeout_seconds=middleware.request_timeout_seconds, retry_interval_seconds=middleware.retry_interval_seconds)
            application.state.ingestion_review_service = review_service
            deletion_reviews = SQLiteDeletionReviewRepository(sqlite)
            deletion_review_service = DeletionReviewService(deletion_reviews, repository, files, service, client,
                timeout_seconds=middleware.request_timeout_seconds, retry_interval_seconds=middleware.retry_interval_seconds)
            application.state.deletion_review_service = deletion_review_service
            if ingestion.enabled and middleware.enabled:
                embedding_config = load_embedding_settings()
                model_config = load_mllm_settings()
                comparison_config = load_file_deduplication_settings()
                passport_config = load_passport_recommendation_settings()
                pdf_config = load_pdf_render_settings()
                embeddings = EmbeddingService(embedding_config)
                resources.push_async_callback(embeddings.close)
                mllm = MLLMClient(model_config)
                resources.push_async_callback(mllm.close)
                candidates = ReadyResourceSearch(repository, model_name=embedding_config.model,
                                                 dimensions=embedding_config.dimensions)
                context = IngestionWorkflowContext(
                    middleware=client, sessions=service, embeddings=embeddings,
                    candidates=candidates, settings=ingestion,
                    resource_repository=repository, reviews=reviews,
                    pdf_service=ResourcePDFService(ingestion_settings=ingestion, render_settings=pdf_config),
                    deduplication_context=FileDeduplicationContext(mllm=mllm, settings=comparison_config),
                    passport_context=PassportRecommendationContext(mllm=mllm, resource_search=candidates,
                                                                   search_settings=passport_config),
                    pending_review_service=PendingReviewService(repository, files.ingestion,
                        max_file_bytes=ingestion.max_file_bytes, reviews=reviews),
                    request_timeout_seconds=middleware.request_timeout_seconds,
                )
                application.state.ingestion_workflow_context = context
                consumer = IngestionConsumerService(context)
                resources.push_async_callback(consumer.close)
                application.state.ingestion_consumer_service = consumer
                # 外部服务在后台首次使用时连接，MySQL 或模型暂时不可用不阻塞 Web 启动。
                await consumer.start()
            if deletion.enabled and middleware.enabled:
                deletion_consumer = DeletionConsumerService(
                    deletion, service, client, repository, files.resources,
                    reviews=deletion_reviews,
                    request_timeout_seconds=middleware.request_timeout_seconds,
                )
                resources.push_async_callback(deletion_consumer.close)
                application.state.deletion_consumer_service = deletion_consumer
                await deletion_consumer.start()
            # 恢复器最后启动、最先关闭，确保恢复期间 SQLite、MySQL 和中间件依赖均存活。
            resources.push_async_callback(review_service.close)
            await review_service.start()
            resources.push_async_callback(deletion_review_service.close)
            await deletion_review_service.start()
            yield

    application = FastAPI(title="知识沉淀注册中心后端", version="0.1.0", lifespan=lifespan)
    application.include_router(auth_router, prefix="/registry/api")
    application.include_router(platform_router, prefix="/registry/api")
    application.include_router(catalog_router, prefix="/registry/api")
    application.include_router(resource_router, prefix="/registry/api")
    application.include_router(ingestion_review_router, prefix="/registry/api")
    application.include_router(deletion_review_router, prefix="/registry/api")

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        # 默认 422 会回显错误输入，登录密钥等敏感字段不得进入响应。
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    return application


app = create_app()


if __name__ == "__main__":
    from pathlib import Path

    import uvicorn

    # 热重载必须使用模块导入字符串；显式监控 app，兼容 IDE 的不同工作目录。
    uvicorn.run("app.main:app", host="127.0.0.1", port=23000, reload=True,
                reload_dirs=[str(Path(__file__).resolve().parent)])
