"""注册中心中央登录验证与管理员查询配置，不包含用户登录和初始化配置。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from app.config.middleware import PROJECT_ROOT


class AuthorizationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    request_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)


def load_authorization_settings(env_file: Path | None = None) -> AuthorizationSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / '.env', interpolate=False), **os.environ}
    try:
        return AuthorizationSettings(
            request_timeout_seconds=values.get('AUTH_REQUEST_TIMEOUT_SECONDS', '5'))
    except (ValueError, TypeError):
        raise ValueError('注册中心授权配置不合法，请检查请求超时') from None
