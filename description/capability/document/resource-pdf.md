# 资源 PDF 读取与内存渲染

> **用途：** 根据来源平台 code 和附件文件名，从资源目录读取 PDF，返回带页码、PNG 字节与页面 UUID 的渲染对象，供文件对比节点使用。

---

## 使用方式

```python
from app.service.resource_pdf import ResourcePDFService

service = ResourcePDFService()
prepared = await service.load("contract-pheno", "request_001.pdf")
for page in prepared.pages:
    print(page.page_number, page.media_uuid, page.width_pixels, page.height_pixels)
    png_bytes = page.png_bytes
```

服务默认使用 `RESOURCE_ROOT` 和 `INGESTION_MAX_FILE_BYTES`，配置读取沿用[入库接收服务](../application/ingestion-consumer.md)。渲染上限默认从 `VLLM_MLLM_` 环境配置加载，见[多模态模型接入](../infrastructure/mllm.md)。可以通过 `root`、`ingestion_settings`、`render_settings` 构造参数覆盖目录和上限，不访问数据库或中间件。读取待审核原文件时显式使用 `root=ingestion_settings.ingestion_file_root`；默认只读取正式资源，不跨目录自动回退。

`file_name` 是资源表 `file_url` 中保存的附件文件名，例如 `request_001.pdf`，不带 `/`。它不是申请的展示名称 `name`。平台 code 与附件名遵循[资源契约](../../architecture/data/resource.md)，文件名由附件标识和后缀组成。

尚未落盘的文件可以调用同一服务的内存入口：

```python
prepared = await service.prepare(
    pdf_bytes, platform_code="contract-pheno", file_name="request_001.pdf",
)
```

---

## 返回对象与页面身份

`PreparedPDF` 包含来源平台、附件文件名、原 PDF 内容 SHA-256 身份、原文件大小、总页数、视觉 token 估算总数及不可变页面元组。`PreparedPDFPage` 包含从 1 开始的页码、PNG 字节、像素宽高、实际缩放比例、视觉 token 估算、PNG 内容指纹和随机 UUIDv4 `media_uuid`。

PNG 字节只驻留内存，不进入模型对象的 `repr` 或 Pydantic 导出结果。原 PDF 字节在渲染完成后不保存在返回对象中，不重写原文件、不生成处理版 PDF、PNG 文件或 Base64。

同一个返回对象的页面 UUID 在其生命周期内保持不变。并行比较时，应准备当前文件一次，并共享该对象；重新调用 `load` 或 `prepare` 会生成新的页面对象和 UUID。本服务不维护跨任务渲染缓存，避免文件替换后读取旧页面。

> **接入边界：** 页面可通过[多模态模型客户端](../infrastructure/mllm.md)完成首次媒体填充、UUID-only 引用和 cache-miss 恢复。UUID 本身不能保证免传输，必须使用该客户端协调发送。入库主图已使用本服务准备当前 PDF、在节点 C 读取候选 PDF，并由[判重子图](../../architecture/workflow/file-deduplication/readme.md)进行多轮模型比较；通行证推荐复用当前文件页面。视觉处理当前仅支持 PDF，不能据此认为其他类型附件已具备智能审核能力。

---

## 渲染与资源限制

实现参考合同服务，使用 PyMuPDF 内存打开 PDF；磁盘读取与渲染均卸载到工作线程，进程内 PyMuPDF 操作共用锁。取消时等待当前线程结束，不遗留继续读取或渲染的后台任务。

`PDFRenderSettings` 的默认值如下，可通过构造参数调整：

环境变量统一以 `VLLM_MLLM_` 开头：`MAX_PDF_PAGES`、`MAX_RENDER_SCALE`、`VISUAL_TOKEN_PATCH_SIZE`、`MAX_VISUAL_TOKENS_PER_PAGE`、`MAX_VISUAL_TOKENS_PER_DOCUMENT`、`MAX_PAGE_PIXELS`、`MAX_PNG_BYTES`。只加载渲染配置时不要求提供模型地址或模型名。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `max_pages` | `100` | 文档最大页数。 |
| `max_render_scale` | `2` | 相对 PDF 页面尺寸的最大渲染比例。 |
| `visual_token_patch_size` | `32` | 估算视觉 token 使用的 patch 边长。 |
| `max_visual_tokens_per_page` | `2048` | 单页视觉 token 估算上限。 |
| `max_visual_tokens_per_document` | `65536` | 单份文档视觉 token 估算总上限。 |
| `max_page_pixels` | `4000000` | 单页最大像素数。 |
| `max_png_bytes` | `134217728` | 所有页面 PNG 的累计字节上限。 |

按 `ceil(width / patch) * ceil(height / patch)` 估算视觉 token；对每页分配不超过文档总预算的额度，再二分计算可用渲染比例，渲染后复核尺寸和预算。保留全部页面，不静默截断。该估算不等同于实际模型 tokenizer，也不表示两份文档一定能同时放入后续模型上下文。

PNG 上限控制每份返回结果的累计大小，不代表整个进程的峰值内存上限；调用方还需要限制同时加载的文档数量，并在任务结束后释放对象。

---

## 读取与失败边界

路径固定为 `{RESOURCE_ROOT}/{platform_code}/{file_name}`。逐级使用目录句柄和 `O_NOFOLLOW` 打开，拒绝目录穿越、任意 URL、编码路径、符号链接和非普通文件；文件长度先检查、再有界读取，并确认读取长度没有变化。文件缺失抛出 `FileNotFoundError`，输入定位不合法抛出 `ValueError`，内容或安全读取失败抛出 `ResourcePDFError`。

拒绝非 PDF、空文件、需密码的文件、无页面文件、无效页面尺寸，以及超过文件大小、页数、像素、视觉预算或 PNG 累计上限的结果。读取失败不创建目录，不落盘，也不推进工作流审核状态。

服务不查询文件的 `name`、`abstract`，这两个业务字段应由申请或候选查询提供。支持 Linux 和 macOS 的目录句柄读取方式。

---

## 依赖与验证

依赖 `PyMuPDF==1.28.2`，由 [requirement.txt](../../../requirement.txt) 安装；无需额外 Poppler 或系统渲染命令。渲染与并发限制依据 [PyMuPDF 图像说明](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)和[并发说明](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)。

```bash
python -m unittest discover -s tests -p 'test_resource_pdf.py'
```

测试使用公开的四页 PDF，覆盖完整页面、PNG 指纹与 UUID、内存入口、五并发读取、预算缩放、路径与链接隔离、文件缺失、非普通文件、损坏文件及资源上限。测试不调用外部模型。
