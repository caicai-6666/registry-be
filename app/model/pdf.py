"""共享的 PDF 内存页面，页面引用与 PNG 字节具有相同生命周期。"""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PreparedPDFPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_number: int
    png_bytes: bytes = Field(repr=False, exclude=True)
    width_pixels: int
    height_pixels: int
    render_scale: float
    visual_tokens: int
    content_sha256: str
    media_uuid: str = Field(default_factory=lambda: str(uuid4()))


class PreparedPDF(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_platform: str
    file_name: str
    document_id: str
    source_file_size_bytes: int
    page_count: int
    total_visual_tokens: int
    pages: tuple[PreparedPDFPage, ...]
