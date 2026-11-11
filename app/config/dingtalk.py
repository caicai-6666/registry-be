"""钉钉通讯录刷新配置，与用户登录配置独立。"""
import os
from pathlib import Path

from dotenv import dotenv_values
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.config.middleware import PROJECT_ROOT


class DingTalkSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', hide_input_in_errors=True)

    app_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    app_secret: SecretStr = Field(default_factory=lambda: SecretStr(''))
    proxy_url: SecretStr = Field(default_factory=lambda: SecretStr(''),
        description='仅钉钉 HTTP 请求使用的 HTTP/HTTPS 代理根地址，可携带代理认证；空值直连。')
    timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    refresh_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_concurrency: int = Field(default=5, ge=1, le=20)

    @field_validator('proxy_url')
    @classmethod
    def validate_proxy_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            return value
        try:
            url = httpx.URL(raw)
            if (raw != raw.strip() or any(char.isspace() or ord(char) < 32 for char in raw)
                    or url.scheme not in ('http', 'https') or not url.host
                    or url.path not in ('', '/') or url.query or url.fragment
                    or (url.port is not None and not 1 <= url.port <= 65535)):
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            # 代理可能含认证信息，校验异常不回显输入；SecretStr 同时保护模型 repr。
            raise ValueError('钉钉代理须为有效的 HTTP/HTTPS 根地址，不含路径、查询参数或片段') from None
        return value

    @model_validator(mode='after')
    def validate_credentials(self):
        key,secret=self.app_key.get_secret_value(),self.app_secret.get_secret_value()
        if bool(key.strip()) != bool(secret.strip()):
            raise ValueError('钉钉 AppKey 和 AppSecret 必须同时填写')
        if key != key.strip() or secret != secret.strip():
            raise ValueError('钉钉凭据不得含首尾空白')
        return self

    @property
    def configured(self) -> bool:
        return bool(self.app_key.get_secret_value())


def load_dingtalk_settings(env_file: Path | None = None) -> DingTalkSettings:
    values={**dotenv_values(env_file or PROJECT_ROOT/'.env',interpolate=False),**os.environ}
    try:
        return DingTalkSettings(app_key=values.get('DINGTALK_APP_KEY') or '',
            app_secret=values.get('DINGTALK_APP_SECRET') or '',
            proxy_url=values.get('DINGTALK_PROXY_URL') or '',
            timeout_seconds=values.get('DINGTALK_TIMEOUT_SECONDS','10'),
            refresh_timeout_seconds=values.get('DINGTALK_REFRESH_TIMEOUT_SECONDS','300'),
            max_concurrency=values.get('DINGTALK_REFRESH_MAX_CONCURRENCY','5'))
    except (ValueError,TypeError):
        raise ValueError('钉钉通讯录配置不合法，请检查凭据、代理地址、超时和并发上限') from None
