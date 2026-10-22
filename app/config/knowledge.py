"""知识后端 HTTP 接入配置，与中间件平台会话配置独立。"""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.config.middleware import PROJECT_ROOT


class KnowledgeSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    base_url: HttpUrl | None = None
    timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    avatar_max_bytes: int = Field(default=10485760, gt=0)
    avatar_cache_seconds: int = Field(default=86400, ge=0)

    @model_validator(mode='after')
    def validate_root(self):
        if self.base_url is not None and (self.base_url.path not in (None, '/') or
                self.base_url.query is not None or self.base_url.fragment is not None or
                self.base_url.username is not None or self.base_url.password is not None):
            raise ValueError('知识后端地址须为不含凭据、接口路径或查询参数的服务根地址')
        return self


def load_knowledge_settings(env_file: Path | None = None) -> KnowledgeSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / '.env', interpolate=False), **os.environ}
    try:
        return KnowledgeSettings(base_url=(values.get('KNOWLEDGE_ACCUMULATION_BASE_URL') or '').strip() or None,
            timeout_seconds=values.get('KNOWLEDGE_ACCUMULATION_TIMEOUT_SECONDS', '10'),
            avatar_max_bytes=values.get('KNOWLEDGE_ACCUMULATION_AVATAR_MAX_BYTES', '10485760'),
            avatar_cache_seconds=values.get('KNOWLEDGE_ACCUMULATION_AVATAR_CACHE_SECONDS', '86400'))
    except (ValueError, TypeError):
        raise ValueError('知识后端接入配置不合法，请检查根地址、超时、头像大小及缓存时长') from None
