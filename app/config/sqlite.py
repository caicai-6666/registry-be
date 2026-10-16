"""SQLite 文件路径与锁等待配置，路径以项目根目录为基准。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config.middleware import PROJECT_ROOT


class SQLiteSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path = PROJECT_ROOT / "data/review-log/registry.db"
    busy_timeout_seconds: float = Field(default=5, gt=0, le=600, allow_inf_nan=False)

    @field_validator("path", mode="before")
    @classmethod
    def resolve_path(cls, value):
        if not str(value).strip() or str(value) == ":memory:" or str(value).startswith("file:"):
            raise ValueError("SQLite 必须配置持久化文件路径")
        path = Path(value)
        return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def load_sqlite_settings(env_file: Path | None = None) -> SQLiteSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return SQLiteSettings(
            path=values.get("SQLITE_PATH", str(Path(values.get("REVIEW_LOG_ROOT") or "data/review-log") / "registry.db")),
            busy_timeout_seconds=values.get("SQLITE_BUSY_TIMEOUT_SECONDS", "5"),
        )
    except (ValueError, TypeError, ValidationError):
        raise ValueError("SQLite 配置不合法，请检查数据库文件路径与锁等待时间") from None
