"""推荐子图的搜索、模型及并发依赖，不随输入输出传递。"""

from dataclasses import dataclass
from collections.abc import Awaitable, Callable

from app.infrastructure.mllm import MLLMClient
from app.config.passport_recommendation import PassportRecommendationSettings
from app.service.resource_candidates import ReadyResourceSearch
from app.model.pdf import PreparedPDF
from app.agent.passport_recommendation.state import PassportGroup, PassportGroupJudgment, PassportRecommendationInput

GroupSearch = Callable[[PassportRecommendationInput], Awaitable[list[PassportGroup | dict]]]
GroupJudge = Callable[[PassportRecommendationInput, PassportGroup], Awaitable[PassportGroupJudgment | dict]]


@dataclass(frozen=True)
class PassportRecommendationContext:
    mllm: MLLMClient | None = None
    group_search: GroupSearch | None = None
    group_judge: GroupJudge | None = None
    judgment_concurrency: int = 10
    resource_search: ReadyResourceSearch | None = None
    search_settings: PassportRecommendationSettings | None = None
    prepare_pdf: Callable[[], Awaitable[PreparedPDF]] | None = None
    # 每组完成或失败时分别提交原始审计，模型正文与原生思考不进入其他组上下文。
    audit_sink: Callable[[list[dict]], None] | None = None

    def __post_init__(self):
        if type(self.judgment_concurrency) is not int or self.judgment_concurrency < 1:
            raise ValueError("通行证组判断并发必须为正整数")
