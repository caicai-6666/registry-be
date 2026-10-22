"""入库人工审核列表、审核历史与最终审核决定接口。"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.router.dependency import (
    IngestionReviewServiceDependency, PlatformAdminUserDependency, ReviewID,
    ingestion_review_error,
)
from app.schema.ingestion_review import (
    IngestionReviewDecision, IngestionReviewResultResponse, PendingIngestionReviewResponse, IngestionReviewHistoryResponse,
)

router = APIRouter(prefix="/ingestion-review", tags=["ingestion-review"],
    responses={403: {"description": "当前用户无权管理注册中心平台"}, 401: {"description": "access-token 缺失、无效或已过期"}, 503: {"description": "审核存储或依赖服务不可用"}})


@router.get("/list", response_model=list[PendingIngestionReviewResponse], summary="获取待入库审核列表")
async def list_pending_ingestion(response: Response, service: IngestionReviewServiceDependency):
    try:
        rows = await service.reviews.list_pending()
        result = [PendingIngestionReviewResponse.model_validate(row) for row in rows]
    except Exception as error:
        raise ingestion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/history", response_model=list[IngestionReviewHistoryResponse], summary="按审核员获取入库审核历史")
async def list_ingestion_history(response: Response, service: IngestionReviewServiceDependency,
                                 current_reviewer: PlatformAdminUserDependency,
                                 reviewer: Annotated[str | None, Query(min_length=1, max_length=200, pattern=r"\S",
                                     description="最终审核员标识（新记录为 userid，历史可能为姓名），精确匹配；省略时查询当前登录审核员。")]=None):
    try:
        rows = await service.reviews.list_history(reviewer if reviewer is not None else current_reviewer.userid)
        result = [IngestionReviewHistoryResponse.model_validate(row) for row in rows]
    except Exception as error:
        raise ingestion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{review_id}/decision", response_model=IngestionReviewResultResponse, summary="确定入库最终审核结果",
    responses={404: {"description": "审核申请或待审原件不存在"}, 409: {"description": "决定已确定或正式源文件身份冲突"}})
async def decide_ingestion(review_id: ReviewID, body: IngestionReviewDecision, response: Response,
                           service: IngestionReviewServiceDependency, reviewer: PlatformAdminUserDependency):
    try:
        result = await service.decide(review_id, body, reviewer.userid)
    except Exception as error:
        raise ingestion_review_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result
