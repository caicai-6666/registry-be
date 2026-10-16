"""正式文件目录路由，读取 MySQL 资源表的公开基本信息。"""

import logging

import aiomysql
from fastapi import APIRouter, Depends, HTTPException, Response

from app.router.dependency import ResourceRepositoryDependency, require_authenticated_user
from app.schema.resource import ResourceBasicInfoResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(require_authenticated_user)],
    responses={401: {"description": "免登码缺失、无效或已过期"},
               503: {"description": "正式资源仓库不可用或查询失败"}})


@router.get("/list", response_model=list[ResourceBasicInfoResponse], summary="获取所有正式文件基本信息")
async def list_catalog_files(response: Response, repository: ResourceRepositoryDependency) -> list[ResourceBasicInfoResponse]:
    try:
        rows = await repository.list_basic_info()
    except (aiomysql.MySQLError, OSError, TimeoutError, ValueError, RuntimeError) as error:
        # 不回显数据库地址、账号、SQL 或错误原文，也不将查询失败伪装为空资源列表。
        logger.warning("正式文件基本信息查询失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="正式文件查询失败，请稍后重试",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return [ResourceBasicInfoResponse.model_validate(row) for row in rows]
