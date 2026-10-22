"""删除人工审核列表、审核历史与最终审核决定接口。"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.router.dependency import (
    DeletionReviewServiceDependency, DeletionReviewID, PlatformAdminUserDependency,
    deletion_review_error,
)
from app.schema.deletion_review import (
    DeletionReviewDecision, DeletionReviewResultResponse,
    PendingDeletionReviewResponse, DeletionReviewHistoryResponse,
)

router = APIRouter(prefix="/deletion-review", tags=["deletion-review"],
    responses={403: {"description": "当前用户无权管理注册中心平台"}, 401: {"description": "access-token 缺失、无效或已过期"}, 503: {"description": "审核存储或依赖服务不可用"}})


@router.get("/list", response_model=list[PendingDeletionReviewResponse], summary="获取待删除审核列表")
async def list_pending_deletion(response: Response, service: DeletionReviewServiceDependency):
    try:
        rows = await service.reviews.list_pending()
        result = [PendingDeletionReviewResponse.model_validate(row) for row in rows]
    except Exception as error:
        raise deletion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/history", response_model=list[DeletionReviewHistoryResponse], summary="按审核员获取删除审核历史")
async def list_deletion_history(response: Response, service: DeletionReviewServiceDependency,
                                current_reviewer: PlatformAdminUserDependency,
                                reviewer: Annotated[str | None, Query(min_length=1, max_length=200, pattern=r"\S",
                                    description="精确匹配删除审核员或该文件的入库审核员标识（新记录为 userid，历史可能为姓名）；省略时使用当前登录审核员。")]=None):
    try:
        rows = await service.reviews.list_history(reviewer if reviewer is not None else current_reviewer.userid)
        result = [DeletionReviewHistoryResponse.model_validate(row) for row in rows]
    except Exception as error:
        raise deletion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{review_id}/decision", response_model=DeletionReviewResultResponse, summary="确定删除最终审核结果",
    responses={404: {"description": "审核申请或原件不存在"}, 409: {"description": "决定已确定或正式资源引用冲突"}})
async def decide_deletion(review_id: DeletionReviewID, body: DeletionReviewDecision, response: Response,
                          service: DeletionReviewServiceDependency, reviewer: PlatformAdminUserDependency):
    try:
        result = await service.decide(review_id, body, reviewer.userid)
    except Exception as error:
        raise deletion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result
