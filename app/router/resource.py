"""正式文件原件读取接口，按文件类型返回原始内容。"""

import logging
import mimetypes
from typing import Annotated

import aiomysql
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from app.router.dependency import (
    ResourceRepositoryDependency, require_authenticated_user, IngestionReviewServiceDependency,
    ReviewID, ingestion_review_error,
)
from app.service.review_files import ReviewFileService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/resource", tags=["resource"], dependencies=[Depends(require_authenticated_user)],
    responses={
        401: {"description": "免登码缺失、无效或已过期"},
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


@router.get(
    "/file/{resource_id}", response_class=Response, summary="按 ID 获取正式文件原件",
    responses={200: {"description": "未经转换的文件原件，Content-Type 随文件类型变化",
                     "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def get_resource_file(
    resource_id: ResourceID, repository: ResourceRepositoryDependency, files: ReviewFileServiceDependency,
) -> Response:
    headers = {"Cache-Control": "no-store"}
    try:
        resource = await repository.find_by_id(resource_id)
    except (aiomysql.MySQLError, OSError, TimeoutError, ValueError, RuntimeError) as error:
        logger.warning("正式文件定位失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="正式文件查询失败，请稍后重试", headers=headers) from None
    if resource is None:
        raise HTTPException(status_code=404, detail="正式资源不存在", headers=headers)
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
    responses={200: {"description": "未经转换的待审原件，Content-Type 随文件类型变化",
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
