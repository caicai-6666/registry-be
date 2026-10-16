"""按平台与附件文件名读取 PDF，并在内存中生成带 UUID 的页面。"""

from hashlib import sha256
from math import ceil, isfinite
from pathlib import Path
from threading import RLock

import pymupdf

from app.config.ingestion import IngestionSettings, load_ingestion_settings
from app.config.pdf import PDFRenderSettings, load_pdf_render_settings
from app.infrastructure.resource_store import ResourceFileStore, file_io, validate_resource_location
from app.model.pdf import PreparedPDF, PreparedPDFPage

# 与合同服务一致，所有 PyMuPDF 操作共用锁；线程卸载仅避免阻塞事件循环。
_PDF_LOCK = RLock()


class ResourcePDFError(ValueError):
    """附件内容、读取行为或渲染结果不满足 PDF 服务契约。"""


class ResourcePDFService:
    def __init__(
        self, root: Path | None = None, *,
        ingestion_settings: IngestionSettings | None = None,
        render_settings: PDFRenderSettings | None = None,
    ):
        settings = ingestion_settings or load_ingestion_settings()
        self._store = ResourceFileStore(root or settings.resource_root)
        self._max_file_bytes = settings.max_file_bytes
        self._render = render_settings or load_pdf_render_settings()

    async def load(self, platform_code: str, file_name: str) -> PreparedPDF:
        """读取 data/resource/{platform_code}/{file_name}，返回完整渲染对象。"""
        self._validate_pdf_location(platform_code, file_name)
        try:
            content = await self._store.read(
                platform_code, file_name, max_file_bytes=self._max_file_bytes,
            )
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:
            raise ResourcePDFError("PDF 附件无法安全读取") from exc
        return await self.prepare(content, platform_code=platform_code, file_name=file_name)

    async def prepare(self, content: bytes, *, platform_code: str, file_name: str) -> PreparedPDF:
        """复用同一渲染能力处理尚未落盘的入库附件。"""
        self._validate_pdf_location(platform_code, file_name)
        if not isinstance(content, bytes) or not 0 < len(content) <= self._max_file_bytes:
            raise ResourcePDFError("PDF 字节为空、类型不合法或超过文件大小上限")
        # file_io 在取消时等待线程退出，避免后台继续渲染并积累无主的大对象。
        return await file_io(self._prepare_sync, content, platform_code, file_name)

    @staticmethod
    def _validate_pdf_location(platform_code: str, file_name: str) -> None:
        validate_resource_location(platform_code, file_name)
        if not file_name.lower().endswith(".pdf"):
            raise ResourcePDFError("当前文件读取服务仅支持 PDF 附件")

    def _prepare_sync(self, content: bytes, platform: str, name: str) -> PreparedPDF:
        with _PDF_LOCK:
            try:
                with pymupdf.open(stream=content, filetype="pdf") as document:
                    if not document.is_pdf or document.needs_pass:
                        raise ResourcePDFError("附件不是 PDF 或需要密码")
                    count = document.page_count
                    if not 0 < count <= self._render.max_pages:
                        raise ResourcePDFError("PDF 页数为空或超过上限")
                    # 整份文件均参与对比；预算不足时缩放页面，不能静默丢弃后续页。
                    budget = min(
                        self._render.max_visual_tokens_per_page,
                        self._render.max_visual_tokens_per_document // count,
                    )
                    if budget < 1:
                        raise ResourcePDFError("文档视觉预算不足以覆盖每一页")
                    pages = []
                    total_bytes = 0
                    for index, page in enumerate(document):
                        rendered = self._render_page(page, index + 1, budget)
                        total_bytes += len(rendered.png_bytes)
                        if total_bytes > self._render.max_png_bytes:
                            raise ResourcePDFError("PDF 页面 PNG 总大小超过内存上限")
                        pages.append(rendered)
                    return PreparedPDF(
                        source_platform=platform, file_name=name,
                        document_id=sha256(content).hexdigest(),
                        source_file_size_bytes=len(content), page_count=count,
                        total_visual_tokens=sum(page.visual_tokens for page in pages),
                        pages=tuple(pages),
                    )
            except ResourcePDFError:
                raise
            except (RuntimeError, ValueError) as exc:
                raise ResourcePDFError("PDF 损坏、格式无效或页面渲染失败") from exc

    def _render_page(self, page: pymupdf.Page, number: int, budget: int) -> PreparedPDFPage:
        rect = page.rect
        if not all(isfinite(value) and value > 0 for value in (rect.width, rect.height)):
            raise ResourcePDFError(f"PDF 第 {number} 页尺寸不合法")
        settings = self._render
        patch = settings.visual_token_patch_size

        def tokens(width, height):
            return ceil(width / patch) * ceil(height / patch)

        def fits(scale):
            width, height = ceil(rect.width * scale), ceil(rect.height * scale)
            return tokens(width, height) <= budget and width * height <= settings.max_page_pixels

        scale = settings.max_render_scale
        if not fits(scale):
            lower, upper = 0.0, scale
            for _ in range(64):
                middle = (lower + upper) / 2
                if fits(middle):
                    lower = middle
                else:
                    upper = middle
            scale = lower * (1 - 1e-6)
        if scale <= 0:
            raise ResourcePDFError(f"PDF 第 {number} 页无法在预算内渲染")
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
        visual_tokens = tokens(pixmap.width, pixmap.height)
        if visual_tokens > budget or pixmap.width * pixmap.height > settings.max_page_pixels:
            raise ResourcePDFError(f"PDF 第 {number} 页渲染结果超过预算")
        png = pixmap.tobytes("png")
        return PreparedPDFPage(
            page_number=number, png_bytes=png, width_pixels=pixmap.width,
            height_pixels=pixmap.height, render_scale=scale,
            visual_tokens=visual_tokens, content_sha256=sha256(png).hexdigest(),
        )
