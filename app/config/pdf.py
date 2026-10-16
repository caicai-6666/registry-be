"""PDF 内存渲染的视觉预算与资源上限。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.middleware import PROJECT_ROOT


class PDFRenderSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_pages: int = Field(default=100, ge=1)
    max_render_scale: float = Field(default=2, gt=0, le=4, allow_inf_nan=False)
    visual_token_patch_size: int = Field(default=32, ge=1, le=64)
    max_visual_tokens_per_page: int = Field(default=2048, ge=1)
    max_visual_tokens_per_document: int = Field(default=65536, ge=1)
    max_page_pixels: int = Field(default=4_000_000, ge=1)
    max_png_bytes: int = Field(default=128 * 1024 * 1024, ge=1)


def load_pdf_render_settings(env_file: Path | None = None) -> PDFRenderSettings:
    values = {**dotenv_values(env_file or PROJECT_ROOT / ".env", interpolate=False), **os.environ}
    names = {
        "max_pages": "MAX_PDF_PAGES", "max_render_scale": "MAX_RENDER_SCALE",
        "visual_token_patch_size": "VISUAL_TOKEN_PATCH_SIZE",
        "max_visual_tokens_per_page": "MAX_VISUAL_TOKENS_PER_PAGE",
        "max_visual_tokens_per_document": "MAX_VISUAL_TOKENS_PER_DOCUMENT",
        "max_page_pixels": "MAX_PAGE_PIXELS", "max_png_bytes": "MAX_PNG_BYTES",
    }
    try:
        return PDFRenderSettings(**{
            field: values["VLLM_MLLM_" + name]
            for field, name in names.items() if "VLLM_MLLM_" + name in values
        })
    except ValidationError:
        raise ValueError("PDF 渲染配置不合法，请检查页数、缩放和视觉预算") from None
