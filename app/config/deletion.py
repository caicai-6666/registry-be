"""删除申请后台消费的独立开关与调度配置。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.middleware import PROJECT_ROOT


class DeletionSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    poll_interval_seconds: float = Field(default=2, gt=0, allow_inf_nan=False)
    retry_interval_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)


def load_deletion_settings(env_file: Path | None = None) -> DeletionSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return DeletionSettings(**{
            name: values["DELETION_" + name.upper()]
            for name in DeletionSettings.model_fields if "DELETION_" + name.upper() in values
        })
    except (ValueError, TypeError, ValidationError):
        raise ValueError("删除接收配置不合法，请检查开关和正数轮询、重试时间") from None
