"""正式文件目录路由，查询正式文件及调整文件等级。"""

import logging

import aiomysql
from fastapi import APIRouter, HTTPException, Request, Response

from app.router.dependency import ResourceRepositoryDependency, PlatformAdminUserDependency
from app.model.file_level_adjustment import FileLevelAdjustmentRequest, FileLevelAdjustmentResponse, FileLevelAdjustmentError, FileLevelAdjustmentHistoryResponse
from app.schema.resource import ResourceBasicInfoResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"],
    responses={403: {"description": "当前用户无权管理注册中心平台"}, 401: {"description": "access-token 缺失、无效或已过期"},
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


@router.post("/adjust-file-level", response_model=FileLevelAdjustmentResponse, summary="调整正式文件等级",
             responses={404: {"description": "正式文件不存在"}, 409: {"description": "等级无变化或存在未完成调整"}})
async def adjust_file_level(body: FileLevelAdjustmentRequest, request: Request, response: Response,
                            user: PlatformAdminUserDependency) -> FileLevelAdjustmentResponse:
    service = getattr(request.app.state, "file_level_adjustment_service", None)
    if service is None:
        raise HTTPException(503, "文件等级调整服务不可用", headers={"Cache-Control": "no-store"})
    try:
        result = await service.adjust(body, user.userid, user.name or user.userid)
    except FileLevelAdjustmentError as error:
        raise HTTPException(error.status_code, {"message": str(error), "log_id": error.log_id,
            "database_updated": error.database_updated}, headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/file-level-history", response_model=list[FileLevelAdjustmentHistoryResponse],
            summary="获取所有文件等级调整历史")
async def list_file_level_history(request: Request, response: Response) -> list[FileLevelAdjustmentHistoryResponse]:
    # 认证及 registry 管理权限由业务路由统一依赖校验，不按当前操作人缩小历史范围。
    service = getattr(request.app.state, "file_level_adjustment_service", None)
    if service is None:
        raise HTTPException(503, "文件等级调整服务不可用", headers={"Cache-Control": "no-store"})
    try:
        rows = await service.list_history()
    except Exception as error:
        logger.warning("文件等级调整历史查询失败：error_type=%s", type(error).__name__)
        raise HTTPException(503, "文件等级调整历史暂不可用，请稍后重试",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return rows
