"""正式文件原件读取接口，按文件类型返回原始内容。"""

import logging
import mimetypes
from typing import Annotated

import aiomysql
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response

from app.infrastructure.knowledge import AvatarProxyError

from app.router.dependency import (
    ResourceRepositoryDependency, IngestionReviewServiceDependency,
    ReviewID, ingestion_review_error, AuthenticatedUserDependency,
    _bearer_access_token,
)
from app.model.file_level import FILE_LEVEL
from app.model.resource import StoredResource
from app.service.review_files import ReviewFileService
from app.schema.resource_access import ServiceFileRequest
from app.service.service_file_auth import ServiceFileAuthError

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/resource", tags=["resource"],
    responses={
        401: {"description": "access-token 缺失、无效或已过期"},
        404: {"description": "正式资源或磁盘原件不存在"},
        503: {"description": "数据库或文件读取服务不可用"},
    },
)


# 普通用户入口独立挂载，仅按文件权限授权，不能继承管理路由组的依赖。
reader_router = APIRouter(prefix="/resource", tags=["resource"], responses={
    401: {"description": "用户 token 缺失、无效或已过期"},
    403: {"description": "未获文件来源平台授权、等级不足或文件等级未知"},
    404: {"description": "正式资源或磁盘原件不存在"},
    503: {"description": "中央认证、数据库或文件读取服务不可用"},
})


service_router = APIRouter(prefix='/resource', tags=['resource'], responses={
    401: {'description': '平台 Bearer 缺失、无效或过期'},
    403: {'description': '调用平台不是 knowledge-accumulation，或目标员工文件权限不足'},
    404: {'description': '正式资源或磁盘原件不存在'},
    503: {'description': '平台认证、员工权限、数据库或文件服务暂不可用'},
})


async def get_review_file_service(request: Request) -> ReviewFileService:
    service = getattr(request.app.state, "review_file_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="正式文件读取服务不可用",
                            headers={"Cache-Control": "no-store"})
    return service


ReviewFileServiceDependency = Annotated[ReviewFileService, Depends(get_review_file_service)]
ResourceID = Annotated[int, Path(gt=0, le=18446744073709551615, description="文件在注册中心的 MySQL 正式资源 ID，非 source_id。")]


async def get_stored_resource(resource_id: ResourceID, repository: ResourceRepositoryDependency) -> StoredResource:
    """定位正式资源；授权由各下载入口的依赖单独完成。"""
    headers = {"Cache-Control": "no-store"}
    try:
        resource = await repository.find_by_id(resource_id)
    except (aiomysql.MySQLError, OSError, TimeoutError, ValueError, RuntimeError) as error:
        logger.warning("正式文件定位失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="正式文件查询失败，请稍后重试", headers=headers) from None
    if resource is None:
        raise HTTPException(status_code=404, detail="正式资源不存在", headers=headers)
    return resource


StoredResourceDependency = Annotated[StoredResource, Depends(get_stored_resource)]


async def require_file_access(
    request: Request, user: AuthenticatedUserDependency, resource: StoredResourceDependency,
) -> StoredResource:
    """先确认中央身份，再按正式文件来源平台比较权威员工等级，不使用管理员旁路。"""
    return await check_employee_file_access(request, user.userid, resource)


async def check_employee_file_access(request: Request, userid: str, resource: StoredResource) -> StoredResource:
    """登录下载与可信后台下载共用实时授权规则，不缓存权限，不按管理员身份放行。"""
    headers = {"Cache-Control": "no-store"}
    if resource.file_level is None:
        raise HTTPException(403, "文件等级未知，暂不允许读取", headers=headers)
    employees = getattr(request.app.state, "employee_repository", None)
    if employees is None:
        raise HTTPException(503, "员工权限查询暂不可用", headers=headers)
    try:
        level = FILE_LEVEL.validate_python(resource.file_level)
        permissions = await employees.find_file_permissions(userid)
    except (aiomysql.MySQLError, OSError, TimeoutError, ValueError, RuntimeError) as error:
        logger.warning("文件权限查询失败：error_type=%s", type(error).__name__)
        raise HTTPException(503, "文件权限查询暂不可用", headers=headers) from None
    # 平台键不存在与显式等级 0 不同；只比较文件实际来源平台，禁止跨平台借用等级。
    if permissions is None or resource.source_platform not in permissions or permissions[resource.source_platform] < level:
        raise HTTPException(403, "无权读取该文件", headers=headers)
    return resource


ReadableResourceDependency = Annotated[StoredResource, Depends(require_file_access)]


@reader_router.get("/authorized-file/{resource_id}", response_class=Response,
    summary="按用户文件权限获取正式文件原件",
    responses={200: {"description": "未经转换的正式文件原件",
                     "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}})
async def get_authorized_resource_file(resource: ReadableResourceDependency,
                                       files: ReviewFileServiceDependency) -> Response:
    return await get_resource_file(resource, files)


async def require_file_service(request: Request,
    authorization: Annotated[str | None, Header(alias='Authorization',
        description='知识后端的中间件平台 Bearer token；不是用户登录 token。')] = None,
) -> None:
    headers = {'Cache-Control': 'no-store', 'WWW-Authenticate': 'Bearer'}
    token = _bearer_access_token(authorization)
    if token is None:
        raise HTTPException(401, '缺少有效的平台 Bearer 令牌', headers=headers)
    service = getattr(request.app.state, 'service_file_auth', None)
    if service is None:
        raise HTTPException(503, '平台认证服务暂不可用', headers={'Cache-Control': 'no-store'})
    try:
        await service.authorize(token)
    except ServiceFileAuthError as error:
        if error.status_code != 401:
            headers.pop('WWW-Authenticate')
        raise HTTPException(error.status_code, str(error), headers=headers) from None


async def require_service_file_access(
    request: Request, body: ServiceFileRequest,
    platform: Annotated[None, Depends(require_file_service)], resource: StoredResourceDependency,
) -> StoredResource:
    # 仅可信平台可声明任务所属员工；必须先认证平台，再定位资源和读取员工权限。
    return await check_employee_file_access(request, body.userid, resource)


@service_router.post('/service-authorized-file/{resource_id}', response_class=Response,
    summary='可信知识后端按员工权限读取正式文件原件',
    responses={200: {'description': '未经转换的正式原件',
                    'content': {'*/*': {'schema': {'type': 'string', 'format': 'binary'}}}}})
async def get_service_authorized_file(
    resource: Annotated[StoredResource, Depends(require_service_file_access)],
    files: ReviewFileServiceDependency,
) -> Response:
    return await get_resource_file(resource, files)


@router.get('/avatar/{filename:path}', response_class=Response, summary='代理获取用户头像',
            responses={200: {'description': '知识后端返回的 WebP 原始字节',
                             'content': {'image/webp': {'schema': {'type': 'string', 'format': 'binary'}}}},
                       404: {'description': '头像不存在'}, 422: {'description': '头像文件名不合法'}})
async def get_user_avatar(request: Request,
        filename: Annotated[str, Path(min_length=37, max_length=37, pattern=r'^[a-f0-9]{32}\.webp$',
                                     description='32 位小写十六进制标识加 .webp，不包含目录。')],
        authorization: Annotated[str, Header(alias='Authorization')]) -> Response:
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    # 路由正则可能忽略尾部换行，必须再对照解码后的原始路径。
    if request.scope['path'].rsplit('/', 1)[-1] != filename:
        raise HTTPException(422, '头像文件名不合法', headers=headers)
    client = getattr(request.app.state, 'knowledge_client', None)
    if client is None:
        raise HTTPException(503, '知识后端头像服务尚未配置或暂不可用', headers=headers)
    try:
        # 业务路由组已校验 Bearer 和管理权，此处转发同一个用户 token，不重新建立登录。
        content = await client.read_avatar(filename, authorization.strip().split()[1])
    except AvatarProxyError as error:
        detail = {401: '用户登录已失效', 403: '无权读取头像', 404: '头像不存在',
                  422: '头像文件名不合法', 503: '头像代理暂不可用'}[error.status_code]
        if error.status_code == 401:
            headers['WWW-Authenticate'] = 'Bearer'
        raise HTTPException(error.status_code, detail, headers=headers) from None
    # 头像更新会生成新文件名；成功响应按配置允许私有缓存，零秒保留 no-store。
    # 错误分支仍使用 no-store，避免缓存登录失效、权限拒绝或暂时故障。
    if client.avatar_cache_seconds > 0:
        headers['Cache-Control'] = f'private, max-age={client.avatar_cache_seconds}'
        headers['Vary'] = 'Authorization'
    return Response(content, media_type='image/webp', headers=headers)


@router.get(
    "/file/{resource_id}", response_class=Response, summary="按 ID 获取正式文件原件",
    responses={403: {"description": "当前用户不是注册中心管理员"},
               200: {"description": "未经转换的文件原件，Content-Type 随文件类型变化",
                     "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def get_resource_file(resource: StoredResourceDependency, files: ReviewFileServiceDependency) -> Response:
    headers = {"Cache-Control": "no-store"}
    try:
        # 从经过校验的目录句柄读取，避免直接向 FileResponse 传路径引入符号链接竞态。
        content = await files.read_resource(resource.source_platform, resource.file.file_url)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件原件不存在", headers=headers) from None
    except (OSError, ValueError, RuntimeError) as error:
        # 路径越界、符号链接和读取失败均不暴露真实路径，也不尝试其他暂存目录。
        logger.warning("正式文件原件读取失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="文件原件读取失败，请稍后重试", headers=headers) from None
    return original_file_response(resource.file.file_url, content)


@router.get("/ingestion/{review_id}", response_class=Response, summary="按审核 ID 获取待入库文件原件",

    responses={403: {"description": "当前用户无权管理注册中心平台"}, 200: {"description": "未经转换的待审原件，Content-Type 随文件类型变化",
                     "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}},
               409: {"description": "该申请已确定审核决定，不能读取待审附件"}})
async def get_pending_ingestion_file(review_id: ReviewID, service: IngestionReviewServiceDependency) -> Response:
    try:
        filename, content = await service.read_pending_file(review_id)
    except Exception as error:
        raise ingestion_review_error(error) from None
    return original_file_response(filename, content)


def original_file_response(filename: str, content: bytes) -> Response:
    # 文件名均已由存储读取校验，只推断类型，不转换原件，未知或压缩类型作为二进制附件返回。
    media_type, encoding = mimetypes.guess_type(filename, strict=False)
    if encoding is not None:
        media_type = None
    # 原件不转码，直接指定媒体头，避免响应类为文本文件自动宣称 UTF-8 编码。
    return Response(content=content, headers={
        "Content-Type": media_type or "application/octet-stream",
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    })
