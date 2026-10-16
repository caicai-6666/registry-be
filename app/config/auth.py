"""审核人员文件与进程内免登码配置。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config.middleware import PROJECT_ROOT


class AuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_user_file: Path = PROJECT_ROOT / "data/user/users.yaml"
    login_code_ttl_seconds: int = Field(default=3600, gt=0)

    @field_validator("reviewer_user_file", mode="before")
    @classmethod
    def resolve_path(cls, value):
        if not str(value).strip():
            raise ValueError("人员文件路径不能为空")
        path = Path(value)
        return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def load_auth_settings(env_file: Path | None = None) -> AuthSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return AuthSettings(
            reviewer_user_file=values.get("REVIEWER_USER_FILE", "data/user/users.yaml"),
            login_code_ttl_seconds=values.get("AUTH_LOGIN_CODE_TTL_SECONDS", "3600"),
        )
    except (ValueError, TypeError):
        raise ValueError("人员系统配置不合法，请检查文件路径与免登码时效") from None
