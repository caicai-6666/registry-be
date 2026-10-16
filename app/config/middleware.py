"""中间件接入配置，进程环境优先于项目根目录的 .env。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MiddlewareSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: HttpUrl | None = None
    platform_code: str = Field(default="registry", pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    retry_interval_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    request_timeout_seconds: float = Field(default=40, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_base_url(self):
        if self.base_url is not None and (
            self.base_url.path not in (None, "/")
            or self.base_url.query is not None
            or self.base_url.fragment is not None
            or self.base_url.username is not None
            or self.base_url.password is not None
        ):
            raise ValueError("中间件地址须为不含凭据、接口路径或查询参数的 HTTP/HTTPS 根地址")
        return self

    @property
    def enabled(self) -> bool:
        return self.base_url is not None and bool(self.secret.get_secret_value().strip())


def load_middleware_settings(env_file: Path | None = None) -> MiddlewareSettings:
    # 不展开 $ 引用，也不修改进程环境，防止改变密钥或污染其他应用实例。
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    try:
        return MiddlewareSettings(
            base_url=(values.get("MIDDLEWARE_BASE_URL") or "").strip() or None,
            platform_code=values.get("MIDDLEWARE_PLATFORM_CODE", "registry"),
            secret=SecretStr(values.get("MIDDLEWARE_SECRET") or ""),
            retry_interval_seconds=values.get("MIDDLEWARE_RETRY_INTERVAL_SECONDS", "30"),
            request_timeout_seconds=values.get("MIDDLEWARE_REQUEST_TIMEOUT_SECONDS", "40"),
        )
    except ValidationError:
        # 原始校验错误可能含输入值，启动错误不暴露地址或凭据原文。
        raise ValueError("中间件配置不合法，请检查地址、平台代号和正数超时/重试间隔") from None
