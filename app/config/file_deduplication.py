"""文件比较会话的独立配置，不复用消息接收或模型并发配置。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from app.config.middleware import PROJECT_ROOT


class FileDeduplicationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_visible_pages: int = Field(default=3, ge=1, le=100)
    max_rounds: int = Field(default=32, ge=1, le=256)
    max_consecutive_errors: int = Field(default=3, ge=1, le=32)
    max_page_reads: int = Field(default=24, ge=1, le=256)
    max_observations: int = Field(default=128, ge=1, le=1024)


def load_file_deduplication_settings(env_file: Path | None = None) -> FileDeduplicationSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    return FileDeduplicationSettings(**{
        name: values["FILE_DEDUPLICATION_" + name.upper()]
        for name in FileDeduplicationSettings.model_fields
        if "FILE_DEDUPLICATION_" + name.upper() in values
    })
