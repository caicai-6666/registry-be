"""入库申请后台接收配置。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config.middleware import PROJECT_ROOT


class IngestionSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    resource_root: Path = PROJECT_ROOT / "data/resource"
    review_log_root: Path | None = None
    poll_interval_seconds: float = Field(default=2, gt=0, allow_inf_nan=False)
    retry_interval_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    download_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_file_bytes: int = Field(default=64 * 1024 * 1024, ge=1)
    duplicate_top_k: int = Field(default=5, ge=1, le=100)
    duplicate_min_similarity: float = Field(default=0.8, ge=-1, le=1, allow_inf_nan=False)
    comparison_concurrency: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def resolve_storage_roots(self):
        resource = self.resource_root
        resource = (resource if resource.is_absolute() else PROJECT_ROOT / resource).resolve()
        review = self.review_log_root if self.review_log_root is not None else resource.parent / "review-log"
        review = (review if review.is_absolute() else PROJECT_ROOT / review).resolve()
        if resource.is_relative_to(review) or review.is_relative_to(resource):
            raise ValueError("正式资源与审核日志目录不能重叠")
        object.__setattr__(self,"resource_root",resource)
        object.__setattr__(self,"review_log_root",review)
        return self

    @property
    def ingestion_file_root(self) -> Path:
        return self.review_log_root / "tmp-file/ingestion"

    @property
    def deletion_file_root(self) -> Path:
        return self.review_log_root / "tmp-file/deletion"


def load_ingestion_settings(env_file: Path | None = None) -> IngestionSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        directory = values.get("RESOURCE_ROOT", "data/resource")
        if not directory or not directory.strip():
            raise ValueError("资源目录不能为空")
        root = Path(directory)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        review_directory = values.get("REVIEW_LOG_ROOT")
        if review_directory is not None and not review_directory.strip():
            raise ValueError("审核日志目录不能为空")
        return IngestionSettings(
            enabled=values.get("INGESTION_ENABLED", "true"),
            resource_root=root.resolve(),
            review_log_root=review_directory,
            poll_interval_seconds=values.get("INGESTION_POLL_INTERVAL_SECONDS", "2"),
            retry_interval_seconds=values.get("INGESTION_RETRY_INTERVAL_SECONDS", "5"),
            download_timeout_seconds=values.get("INGESTION_DOWNLOAD_TIMEOUT_SECONDS", "300"),
            max_file_bytes=values.get("INGESTION_MAX_FILE_BYTES", str(64 * 1024 * 1024)),
            duplicate_top_k=values.get("INGESTION_DUPLICATE_TOP_K", "5"),
            duplicate_min_similarity=values.get("INGESTION_DUPLICATE_MIN_SIMILARITY", "0.8"),
            comparison_concurrency=values.get("INGESTION_COMPARISON_CONCURRENCY", "10"),
        )
    except (ValueError, TypeError, ValidationError):
        raise ValueError("入库接收配置不合法，请检查开关、资源目录和正数时间配置") from None
