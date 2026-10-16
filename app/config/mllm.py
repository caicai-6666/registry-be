"""本地 vLLM 多模态 Chat Completions 与 PDF 视觉配置。"""

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError, field_validator, model_validator

from app.config.middleware import PROJECT_ROOT
from app.config.pdf import PDFRenderSettings, load_pdf_render_settings


class MLLMGenerationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enable_thinking: bool = False
    reasoning_effort: Literal["low", "medium", "xhigh"] = "xhigh"
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    top_p: float = Field(default=0.8, gt=0, le=1, allow_inf_nan=False)
    top_k: int = Field(default=20, ge=0)
    presence_penalty: float = Field(default=1.5, ge=-2, le=2, allow_inf_nan=False)
    repetition_penalty: float = Field(default=1, gt=0, allow_inf_nan=False)
    seed: int = 3407
    max_completion_tokens: int = Field(default=8192, ge=1)


class MLLMSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["vllm"] = "vllm"
    endpoint: Literal["chat_completions"] = "chat_completions"
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_concurrent_requests: int = Field(default=20, ge=1)
    use_media_references: bool = True
    context_window_tokens: int = Field(default=262144, ge=1)
    reserved_prompt_tokens: int = Field(default=4096, ge=0)
    reserved_runtime_tokens: int = Field(default=10240, ge=0)
    max_visual_tokens_per_request: int = Field(default=131072, ge=1)
    generation: MLLMGenerationSettings = Field(default_factory=MLLMGenerationSettings)
    vision: PDFRenderSettings = Field(default_factory=PDFRenderSettings)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("MLLM 模型名不能为空或包含首尾空格")
        return value

    @model_validator(mode="after")
    def validate_configuration(self):
        url = self.base_url
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("MLLM 地址不能包含凭据、查询参数或片段")
        available = self.context_window_tokens - self.generation.max_completion_tokens - self.reserved_prompt_tokens - self.reserved_runtime_tokens
        if available <= 0 or self.max_visual_tokens_per_request > available:
            raise ValueError("MLLM 生成与视觉预算超过上下文窗口")
        if self.vision.max_visual_tokens_per_document > self.max_visual_tokens_per_request:
            raise ValueError("MLLM 文档视觉预算不能超过请求预算")
        return self

    @property
    def api_base_url(self) -> str:
        address = str(self.base_url).rstrip("/")
        return address if address.endswith("/v1") else address + "/v1"


def load_mllm_settings(env_file: Path | None = None) -> MLLMSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}

    def value(name, default):
        return values.get("VLLM_MLLM_" + name, default)

    try:
        return MLLMSettings(
            provider=value("PROVIDER", "vllm"), endpoint=value("ENDPOINT", "chat_completions"),
            base_url=value("BASE_URL", ""), model=value("MODEL", ""),
            api_key=SecretStr(value("API_KEY", "") or ""),
            timeout_seconds=value("TIMEOUT_SECONDS", "300"),
            max_concurrent_requests=value("MAX_CONCURRENT_REQUESTS", "20"),
            use_media_references=value("USE_MEDIA_REFERENCES", "true"),
            context_window_tokens=value("CONTEXT_WINDOW_TOKENS", "262144"),
            reserved_prompt_tokens=value("RESERVED_PROMPT_TOKENS", "4096"),
            reserved_runtime_tokens=value("RESERVED_RUNTIME_TOKENS", "10240"),
            max_visual_tokens_per_request=value("MAX_VISUAL_TOKENS_PER_REQUEST", "131072"),
            generation=MLLMGenerationSettings(
                enable_thinking=value("ENABLE_THINKING", "false"),
                reasoning_effort=value("REASONING_EFFORT", "xhigh"),
                temperature=value("TEMPERATURE", "0.7"), top_p=value("TOP_P", "0.8"),
                top_k=value("TOP_K", "20"), presence_penalty=value("PRESENCE_PENALTY", "1.5"),
                repetition_penalty=value("REPETITION_PENALTY", "1.0"), seed=value("SEED", "3407"),
                max_completion_tokens=value("MAX_COMPLETION_TOKENS", "8192"),
            ),
            vision=load_pdf_render_settings(env_file),
        )
    except (ValidationError, TypeError, ValueError):
        # 不输出原始配置或密钥，避免错误日志携带本地认证信息。
        raise ValueError("MLLM 配置不完整或不合法，请检查地址、模型、并发、生成和视觉预算") from None
