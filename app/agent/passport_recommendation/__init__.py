"""文件通行证推荐子图及其输入输出契约。"""

from app.agent.passport_recommendation.state import (
    PassportGroup, PassportGroupFile, PassportGroupJudgment, PassportRecommendationEvidence,
    PassportRecommendation, PassportRecommendationInput, PassportRecommendationResult,
)
from app.agent.passport_recommendation.workflow import build_passport_recommendation_workflow

__all__ = ["PassportGroup", "PassportGroupFile", "PassportGroupJudgment", "PassportRecommendationEvidence", "PassportRecommendation",
           "PassportRecommendationInput", "PassportRecommendationResult", "build_passport_recommendation_workflow"]
