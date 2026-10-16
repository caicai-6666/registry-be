"""通行证推荐的独立搜索配置，不复用入库判重阈值。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.middleware import PROJECT_ROOT


class PassportRecommendationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_similarity: float = Field(default=0.8, ge=-1, le=1, allow_inf_nan=False)
    top_k: int = Field(default=100, ge=1, le=100)


def load_passport_recommendation_settings(env_file: Path | None = None) -> PassportRecommendationSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return PassportRecommendationSettings(
            min_similarity=values.get("PASSPORT_RECOMMENDATION_MIN_SIMILARITY", "0.8"),
            top_k=values.get("PASSPORT_RECOMMENDATION_TOP_K", "100"),
        )
    except (ValueError, TypeError, ValidationError):
        raise ValueError("通行证推荐配置不合法，请检查相似度阈值和 Top-K 数量") from None
