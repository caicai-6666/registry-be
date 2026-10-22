"""正式文件原件读取接口，按文件类型返回原始内容。"""

import logging
import mimetypes
from typing import Annotated

import aiomysql
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response

from app.infrastructure.knowledge import AvatarProxyError

from app.router.dependency import (
    ResourceRepositoryDependency, IngestionReviewServiceDependency,
    ReviewID, ingestion_review_error,
)
from app.model.resource import StoredResource
from app.service.review_files import ReviewFileService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/resource", tags=["resource"],
    responses={
        401: {"description": "access-token 缺失、无效或已过期"},
        404: {"description": "正式资源或磁盘原件不存在"},
        503: {"description": "数据库或文件读取服务不可用"},
    },
)


async def get_review_file_service(request: Request) -> ReviewFileService:
    service = getattr(request.app.state, "review_file_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="正式文件读取服务不可用",
                            headers={"Cache-Control": "no-store"})
    return service


ReviewFileServiceDependency = Annotated[ReviewFileService, Depends(get_review_file_service)]
ResourceID = Annotated[int, Path(gt=0, le=18446744073709551615, description="文件在注册中心的 MySQL 正式资源 ID，非 source_id。")]


async def get_stored_resource(resource_id: ResourceID, repository: ResourceRepositoryDependency) -> StoredResource:
    """管理员依赖先行授权，此处只定位正式资源，不再根据文件等级绕过管理权。"""
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
