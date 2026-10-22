"""跨业务路由复用的 FastAPI 依赖。"""

from typing import Annotated
import logging

from fastapi import Depends, Header, HTTPException, Path, Request, status

from app.service.auth import AuthService
from app.repository.resource import MySQLResourceRepository
from app.auth.model import AuthenticatedUser
from app.service.platform_admin import PlatformAdminService
from app.auth.error import AuthUnavailable
from app.service.ingestion_review import IngestionReviewService
from app.service.deletion_review import DeletionReviewService
from app.model.review import ReviewConflict, ReviewNotFound, ReviewUnavailable


def get_auth_service(request: Request) -> AuthService:
    """从应用生命周期中取得进程内认证服务。"""
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="中央认证服务暂不可用", headers={"Cache-Control": "no-store"})
    return service


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


async def get_resource_repository(request: Request) -> MySQLResourceRepository:
    """目录查询和原件读取共用生命周期管理的正式资源仓库。"""
    repository = getattr(request.app.state, "resource_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="正式资源仓库不可用", headers={"Cache-Control": "no-store"})
    return repository


ResourceRepositoryDependency = Annotated[MySQLResourceRepository, Depends(get_resource_repository)]


async def get_ingestion_review_service(request: Request) -> IngestionReviewService:
    service = getattr(request.app.state, "ingestion_review_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="入库审核服务不可用", headers={"Cache-Control": "no-store"})
    return service


IngestionReviewServiceDependency = Annotated[IngestionReviewService, Depends(get_ingestion_review_service)]
ReviewID = Annotated[int, Path(gt=0, le=9223372036854775807, description="SQLite 入库审核记录 ID，非正式资源 ID 或 source_id。")]


async def get_deletion_review_service(request: Request) -> DeletionReviewService:
    service = getattr(request.app.state, "deletion_review_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="删除审核服务不可用", headers={"Cache-Control": "no-store"})
    return service


DeletionReviewServiceDependency = Annotated[DeletionReviewService, Depends(get_deletion_review_service)]
DeletionReviewID = Annotated[int, Path(gt=0, le=9223372036854775807, description="SQLite 删除审核记录 ID，非正式资源 ID 或 source_id。")]


def deletion_review_error(error: Exception) -> HTTPException:
    if isinstance(error, ReviewNotFound):
        code, detail = 404, str(error)
    elif isinstance(error, FileNotFoundError):
        code, detail = 404, "正式原件或删除保留文件不存在"
    elif isinstance(error, ReviewConflict):
        code, detail = 409, str(error)
    elif isinstance(error, ReviewUnavailable):
        code, detail = 503, str(error)
    else:
        logging.getLogger(__name__).warning("删除审核请求失败：error_type=%s", type(error).__name__)
        code, detail = 503, "删除审核服务暂不可用，请稍后重试"
    return HTTPException(status_code=code, detail=detail, headers={"Cache-Control": "no-store"})


def ingestion_review_error(error: Exception) -> HTTPException:
    if isinstance(error, ReviewNotFound):
        code, detail = 404, str(error)
    elif isinstance(error, FileNotFoundError):
        code, detail = 404, "待审文件原件不存在"
    elif isinstance(error, ReviewConflict):
        code, detail = 409, str(error)
    elif isinstance(error, ReviewUnavailable):
        code, detail = 503, str(error)
    else:
        # 未预期的存储、网络、路径或契约异常不回显原文，保留真实失败而非返回空列表。
        logging.getLogger(__name__).warning("入库审核请求失败：error_type=%s", type(error).__name__)
        code, detail = 503, "入库审核服务暂不可用，请稍后重试"
    return HTTPException(status_code=code, detail=detail, headers={"Cache-Control": "no-store"})


def _bearer_access_token(authorization: str | None) -> str | None:
    """解析严格的 `Authorization: Bearer <access_token>` 请求头。"""
    if authorization is None:
        return None
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1] or None


async def get_platform_admin_service(request: Request) -> PlatformAdminService:
    service = getattr(request.app.state, 'platform_admin_service', None)
    if service is None:
        raise HTTPException(status_code=503, detail='平台管理员服务暂不可用',
                            headers={'Cache-Control': 'no-store'})
    return service


PlatformAdminServiceDependency = Annotated[PlatformAdminService, Depends(get_platform_admin_service)]


async def require_platform_admin(
    request: Request,
    service: AuthServiceDependency,
    authorization: Annotated[str | None, Header(alias="Authorization",
        description="知识后端签发的中央用户 Bearer token；对应用户须具有 registry 管理权限。")] = None,
) -> AuthenticatedUser:
    """唯一业务认证入口：先确认中央用户已登录，再验证注册中心管理权限。"""
    access_token = _bearer_access_token(authorization)
    try:
        user = await service.resolve_user(access_token) if access_token is not None else None
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                detail="access-token 无效或已过期",
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        # 只有登录验证成功后才取得管理服务并查询名单；不提供只验证登录的依赖出口。
        admins = await get_platform_admin_service(request)
        allowed = await admins.is_current_platform_admin(user)
    except AuthUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error),
                            headers={"Cache-Control": "no-store"}) from None
    if not allowed:
        raise HTTPException(status_code=403, detail='无权管理当前平台',
                            headers={'Cache-Control': 'no-store'})
    return user


PlatformAdminUserDependency = Annotated[AuthenticatedUser, Depends(require_platform_admin)]


__all__ = [
    "DeletionReviewServiceDependency",
    "DeletionReviewID",
    "get_deletion_review_service",
    "deletion_review_error",
    "IngestionReviewServiceDependency",
    "ReviewID",
    "get_ingestion_review_service",
    "ingestion_review_error",
    "ResourceRepositoryDependency",
    "get_resource_repository",
    "AuthServiceDependency",
    "get_auth_service",
    "get_platform_admin_service",
    "PlatformAdminServiceDependency",
    "require_platform_admin",
    "PlatformAdminUserDependency",
]
