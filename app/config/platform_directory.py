"""平台目录后台刷新配置，独立于中间件认证心跳和入库消费。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.middleware import PROJECT_ROOT


class PlatformDirectorySettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    refresh_interval_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    retry_interval_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    stale_after_seconds: float = Field(default=90, gt=0, allow_inf_nan=False)


def load_platform_directory_settings(env_file: Path | None = None) -> PlatformDirectorySettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return PlatformDirectorySettings(**{
            name: values["PLATFORM_DIRECTORY_" + name.upper()]
            for name in PlatformDirectorySettings.model_fields
            if "PLATFORM_DIRECTORY_" + name.upper() in values
        })
    except (ValidationError, TypeError, ValueError):
        raise ValueError("平台目录配置不合法，请检查开关和正数刷新、重试及过期时间") from None
