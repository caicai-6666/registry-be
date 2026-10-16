"""单条入库申请在节点间传递的业务状态，不保存模型对话历史。"""

from dataclasses import dataclass
import operator
from typing import Annotated, TypedDict

from app.model.resource import IngestionDelivery, ResourceCandidate, StoredResource
from app.model.pdf import PreparedPDF
from app.agent.passport_recommendation.state import PassportRecommendation


DuplicateCandidate = ResourceCandidate


@dataclass(frozen=True)
class ComparisonResult:
    resource_id: int
    is_duplicate: bool | None
    error_type: str | None = None
    reason: str | None = None


class IngestionWorkflowState(TypedDict, total=False):
    delivery: IngestionDelivery | None
    committed_review: dict | None
    file_content: bytes
    current_pdf: PreparedPDF | None
    abstract_embedding: list[float]
    duplicate_candidates: list[DuplicateCandidate]
    comparison_candidate: DuplicateCandidate
    comparison_results: Annotated[list[ComparisonResult], operator.add]
    duplicate_source_id: str | None
    is_duplicate: bool | None
    duplicate_reason: str
    passport_recommendations: list[PassportRecommendation] | None
    review_id: int
    pending_review_saved: bool
    rejection_published: bool
    rejection_message_id: str
    acknowledged: bool
