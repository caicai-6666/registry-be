"""知识沉淀注册中心后端的 ASGI 应用入口与服务生命周期。"""

import os

# 路由与头像代理在导入时读取前缀，必须先设置，不能依赖热重载子进程重新导入。
if __name__ == "__main__":
    os.environ["APP_PATH_PREFIX"] = "/dev"

# 直接运行文件时将项目根目录加入导入路径，支持 IDE 和不同工作目录。
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config.api import API_PREFIX
from app.config.knowledge import KnowledgeSettings, load_knowledge_settings
from app.config.dingtalk import DingTalkSettings, load_dingtalk_settings
from app.infrastructure.dingtalk import DingTalkClient
from app.repository.personnel_sync import SQLitePersonnelSyncRepository
from app.service.personnel_refresh import PersonnelRefreshService
from app.repository.user_access_sync import SQLiteUserAccessSyncRepository
from app.service.user_access import UserAccessService
from app.infrastructure.knowledge import KnowledgeClient
from app.repository.employee import MySQLEmployeeRepository
from app.service.auth import AuthService
from app.router.auth import router as auth_router
from app.router.user import router as user_router
from app.router.platform import router as platform_router
from app.repository.file_level_adjustment import SQLiteFileLevelAdjustmentRepository
from app.service.file_level_adjustment import FileLevelAdjustmentService
from app.router.catalog import router as catalog_router
from app.router.resource import router as resource_router, reader_router as resource_reader_router
from app.router.resource import service_router as resource_service_router
from app.service.service_file_auth import ServiceFileAuth
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
from app.config.authorization import AuthorizationSettings, load_authorization_settings
from app.router.dependency import require_platform_admin
from app.service.platform_admin import PlatformAdminService
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
    authorization_settings: AuthorizationSettings | None = None,
    platform_directory_settings: PlatformDirectorySettings | None = None,
    deletion_settings: DeletionSettings | None = None,
    knowledge_settings: KnowledgeSettings | None = None,
    dingtalk_settings: DingTalkSettings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 登录由知识后端建立；registry 在所有环境中直接查询中央用户 token。
        middleware = settings or load_middleware_settings()
        authorization = authorization_settings or load_authorization_settings()
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
        application.state.platform_admin_service = None
        application.state.knowledge_client = None
        application.state.personnel_refresh_service = None
        application.state.user_access_service = None
        application.state.file_level_adjustment_service = None
        application.state.service_file_auth = None
        # 后注册的依赖先关闭；启动中途失败也按同样顺序清理已创建的资源。
        async with AsyncExitStack() as resources:
            knowledge = knowledge_settings or load_knowledge_settings()
            if knowledge.base_url is not None:
                knowledge_client = KnowledgeClient(knowledge)
                application.state.knowledge_client = knowledge_client
                resources.push_async_callback(knowledge_client.close)
            client = None
            employees = MySQLEmployeeRepository()
            resources.push_async_callback(employees.close)
            application.state.employee_repository = employees
            sqlite = SQLiteDatabase(sqlite_settings or load_sqlite_settings())
            resources.push_async_callback(sqlite.close)
            resources.push_async_callback(service.close)
            if middleware.enabled:
                client = MiddlewareClient(str(middleware.base_url), timeout_seconds=middleware.request_timeout_seconds)
                resources.push_async_callback(client.close)
            application.state.sqlite_database = sqlite
            await sqlite.initialize()
            dingtalk_config = dingtalk_settings or load_dingtalk_settings()
            dingtalk = None
            if dingtalk_config.configured:
                dingtalk = DingTalkClient(dingtalk_config)
                resources.push_async_callback(dingtalk.close)
            application.state.personnel_refresh_service = PersonnelRefreshService(
                dingtalk_config, employees, dingtalk, SQLitePersonnelSyncRepository(sqlite), service, client)
            ingestion = ingestion_settings or load_ingestion_settings()
            deletion = deletion_settings or load_deletion_settings()
            files = ReviewFileService(ingestion)
            application.state.review_file_service = files
            await files.initialize()
            directory = PlatformDirectoryService(
                platform_directory_settings or load_platform_directory_settings(), service, middleware=middleware,
            )
            application.state.platform_directory_service = directory
            resources.push_async_callback(directory.close)
            await directory.start()
            application.state.user_access_service = UserAccessService(
                employees, SQLiteUserAccessSyncRepository(sqlite), service, client, directory,
                timeout_seconds=middleware.request_timeout_seconds)
            platform_admins = PlatformAdminService(authorization, employees, service, client)
            application.state.platform_admin_service = platform_admins
            resources.push_async_callback(platform_admins.close)
            application.state.auth_service = AuthService(service, client,
                timeout_seconds=authorization.request_timeout_seconds)
            application.state.service_file_auth = ServiceFileAuth(service, client,
                timeout_seconds=authorization.request_timeout_seconds)
            # 消费和人工审核共用认证、HTTP 连接与正式资源仓库；文件申请只有最终批准才写入正式资源。
            database = None
            if middleware.enabled and (ingestion.enabled or deletion.enabled):
                database = load_mysql_settings()
            await service.start()
            # 正式资源仓库按需连接；关闭后台消费后，管理员仍可查询和审核已有申请。
            repository = MySQLResourceRepository(database)
            resources.push_async_callback(repository.close)
            application.state.resource_repository = repository
            adjustment_logs = SQLiteFileLevelAdjustmentRepository(sqlite)
            await adjustment_logs.initialize()
            application.state.file_level_adjustment_service = FileLevelAdjustmentService(
                repository, adjustment_logs, service, client, timeout_seconds=middleware.request_timeout_seconds)
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
                                                 dimensions=embedding_config.dimensions, embeddings=embeddings)
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
    # 管理业务共用管理员边界；用户和可信后台下载分别显式验证身份，再按文件权限授权。
    api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_platform_admin)], responses={
        401: {'description': '用户 token 缺失、无效或已过期'},
        403: {'description': '用户不是注册中心管理员'},
        503: {'description': '中央认证或管理员依赖不可用'},
    })
    for router in (auth_router, user_router, platform_router, catalog_router, resource_router,
                   ingestion_review_router, deletion_review_router):
        api.include_router(router)
    application.include_router(api)
    application.include_router(resource_reader_router, prefix=API_PREFIX)
    application.include_router(resource_service_router, prefix=API_PREFIX)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        # 默认 422 会回显原始输入，敏感请求字段不得进入响应。
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    return application


app = create_app()


if __name__ == "__main__":
    from pathlib import Path

    import uvicorn

    # 默认关闭热重载；如需开启，模块导入字符串与显式监控目录同样支持开发前缀。
    uvicorn.run("app.main:app", host="127.0.0.1", port=16000, reload=False,
                reload_dirs=[str(Path(__file__).resolve().parent)])
