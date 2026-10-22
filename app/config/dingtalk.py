"""钉钉通讯录刷新配置，与用户登录配置独立。"""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.config.middleware import PROJECT_ROOT


class DingTalkSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    app_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    app_secret: SecretStr = Field(default_factory=lambda: SecretStr(''))
    timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    refresh_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_concurrency: int = Field(default=5, ge=1, le=20)

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
            timeout_seconds=values.get('DINGTALK_TIMEOUT_SECONDS','10'),
            refresh_timeout_seconds=values.get('DINGTALK_REFRESH_TIMEOUT_SECONDS','300'),
            max_concurrency=values.get('DINGTALK_REFRESH_MAX_CONCURRENCY','5'))
    except (ValueError,TypeError):
        raise ValueError('钉钉通讯录配置不合法，请检查凭据、超时和并发上限') from None
