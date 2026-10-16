# 正式文件原件 API

`resource` 路由负责读取正式存储的文件内容，文件基本信息列表见[正式文件目录 API](catalog.md)。本接口支持 PDF、图片、Office 文档及其他已保存的附件类型。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [按 ID 获取正式文件原件](#按-id-获取正式文件原件) | `GET` | `/registry/api/resource/file/{resource_id}` |
| [按审核 ID 获取待入库文件原件](#按审核-id-获取待入库文件原件) | `GET` | `/registry/api/resource/ingestion/{review_id}` |

---

## 按 ID 获取正式文件原件

`GET /registry/api/resource/file/{resource_id}`，通过注册中心的正式资源 ID 拉取未经转换的原始文件。

### 认证方式

携带 `Authorization: Bearer <login_code>`，使用[人员登录接口](auth.md#使用密钥登录)取得的免登码。中间件 access_token 不能用于此接口。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| path | `resource_id` | integer | 是 | MySQL 正式资源主键，范围 `1` 至 `18446744073709551615`，不是 `source_id` 或消息 ID。 |
| header | `Authorization` | string | 是 | `Bearer <login_code>`。 |

无 query 参数，无请求体。`resource_id` 可从[正式文件列表](catalog.md#获取所有正式文件基本信息)取得。

### 成功响应

`200 OK`，响应体是原始文件二进制，不是 JSON、Base64、页面图片或压缩后的文件。

| 响应头 | 值或含义 |
| --- | --- |
| `Content-Type` | 根据保存的文件后缀推断；无法识别时为 `application/octet-stream`。 |
| `Content-Disposition` | `attachment; filename="<file_url>"`，文件名采用保存的附件名。 |
| `Content-Length` | 原件字节数。 |
| `Cache-Control` | `no-store`。 |
| `X-Content-Type-Options` | `nosniff`。 |

常见类型：PDF 为 `application/pdf`，PNG 为 `image/png`，JPEG 为 `image/jpeg`，ZIP 为 `application/zip`；DOCX 与 XLSX 使用各自的 Office MIME 类型，文本为 `text/plain`。文本字节也原样返回，不转码，不声明未检测的 charset。

PDF 成功响应示意：

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="request_001.pdf"
Cache-Control: no-store
X-Content-Type-Options: nosniff

%PDF-...
```

### 错误响应

错误媒体类型为 `application/json`，不会以文件二进制伪装错误。

| 状态码 | 触发条件 | 响应 detail |
| --- | --- | --- |
| `401` | 免登码缺失、无效或过期 | `免登码无效或已过期`。 |
| `422` | ID 不是正整数或超出 MySQL 主键范围 | 参数校验错误列表。 |
| `404` | MySQL 正式资源行不存在 | `正式资源不存在`。 |
| `404` | 资源行存在，但正式目录中原件不存在 | `文件原件不存在`。 |
| `503` | MySQL 配置、连接或查询失败 | `正式文件查询失败，请稍后重试`。 |
| `503` | 应用内仓库或文件服务不可用 | `正式资源仓库不可用` 或 `正式文件读取服务不可用`。 |
| `503` | 文件读取失败、非法路径、符号链接、空文件或超过大小上限 | `文件原件读取失败，请稍后重试`。 |

除认证与参数校验响应外，上述错误均含 `Cache-Control: no-store`。错误不回显数据库配置、真实磁盘路径或异常原文。

### 请求示例

替换资源 ID 与人员免登码；本地直接启动默认端口为 `23000`。保存路径由调用方自行选择，不要求与服务端附件名一致。

```bash
curl --fail 'http://127.0.0.1:23000/registry/api/resource/file/123' \
  -H 'Authorization: Bearer 替换为人员登录免登码' \
  --output './downloaded-file.pdf'
```

### 行为与边界

按 `resources.id` 查询所属平台和 `file_url`，从 `RESOURCE_ROOT/<source_platform>/<file_url>` 读取。默认根目录为 `data/resource`，路径规则见[资源表契约](../architecture/data/resource.md)。平台与文件名来自正式记录，不接受调用方传入路径，也不回退到 SQLite 入库暂存或删除保留目录。同名文件在不同平台下通过资源 ID 区分。

复用 `ReviewFileService.read_resource` 和安全的文件目录句柄读取能力，拒绝路径越界、目录和文件符号链接、非普通文件。磁盘读取在线程中执行，沿用 `INGESTION_MAX_FILE_BYTES` 大小上限，默认 `64 MiB`；有界读入内存后返回原字节，不渲染 PDF，不触发模型、不解压、不移动或修改文件。

媒体类型根据后缀推断，不校验文件实际格式。若后缀表示 gzip 等传输编码，则以 `application/octet-stream` 返回压缩原件，不添加 `Content-Encoding` 让客户端自动解压。未知后缀可以正常下载。

读取不依赖平台存活、中间件登录或后台消费开关，与目录接口共用生命周期管理的 MySQL 仓库。待审核入库文件尚无正式资源 ID，使用下述审核 ID 接口读取；最终删除后，资源移除或正式原件已迁走时返回 `404`。

---

## 按审核 ID 获取待入库文件原件

`GET /registry/api/resource/ingestion/{review_id}`，拉取 SQLite 待入库申请对应的原始附件。

### 认证方式

携带人员登录取得的 `Authorization: Bearer <login_code>`，中间件令牌不用于此接口。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| path | `review_id` | integer | 是 | SQLite 审核记录 ID，范围 `1` 至 `9223372036854775807`，从[待审列表](ingestion-review.md#获取待入库审核列表)取得。 |
| header | `Authorization` | string | 是 | Bearer 人员免登码。 |

无 query 参数，无请求体。此 ID 不是正式资源 ID。

### 成功响应

`200 OK`，响应体为未经转换的文件二进制。媒体类型按附件后缀推断，未知类型为 `application/octet-stream`；下载头为 `Content-Disposition: attachment; filename="<file_url>"`，含原件 `Content-Length`、`Cache-Control: no-store` 与 `X-Content-Type-Options: nosniff`。

PDF 响应示意：

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="request_001.pdf"
Cache-Control: no-store

%PDF-...
```

MP3 为 `audio/mpeg`，MP4 为 `video/mp4`，其他类别遵循正式原件接口相同的后缀推断规则。

### 错误响应

| 状态码 | 条件 | detail |
| --- | --- | --- |
| `401` | 人员身份无效 | `免登码无效或已过期`。 |
| `422` | 审核 ID 不合法 | 参数校验错误列表。 |
| `404` | 审核记录不存在 | `入库审核申请不存在`。 |
| `404` | 文件引用或暂存原件不存在 | `待审文件原件不存在`。 |
| `409` | 决定已确定、申请已通过或拒绝 | `该申请已确定审核决定，不能读取待审附件`。 |
| `503` | 审核服务、SQLite 或附件读取失败 | `入库审核服务不可用` 或 `入库审核服务暂不可用，请稍后重试`。 |

错误为 JSON；除认证与参数校验外，均含 `Cache-Control: no-store`，不回显磁盘路径或异常原文。

### 请求示例

替换审核 ID 与人员免登码，本地默认端口为 `23000`。

```bash
curl --fail 'http://127.0.0.1:23000/registry/api/resource/ingestion/12' \
  -H 'Authorization: Bearer 替换为人员登录免登码' \
  --output './pending-file.pdf'
```

### 行为与边界

仅从 SQLite 的 `pending` 且未确定 `decision` 的申请定位附件，读取根目录为 `REVIEW_LOG_ROOT/tmp-file/ingestion`。不查询 MySQL，不回退到 resource 或删除保留目录。读取沿用安全平台名、附件名、目录句柄和文件大小限制，不修改原件。

同一进程内与该申请的审核处理共用锁。审批完成后，待审接口返回 `409`；通过时改用正式 `resource_id` 拉取原件，拒绝附件保留但此接口不提供历史审核附件访问。

---

## 验证

```bash
python -m unittest tests.test_resource_api tests.test_catalog_api
```

测试使用真实 PDF 样本与隔离文件目录，所有临时数据位于 `tests/tmp` 并自动清理。覆盖原件字节一致性、多类型媒体头、未知类型、人员认证、ID 范围、跨平台同名、缺失原件、暂存目录隔离、符号链接、路径校验及错误脱敏。隔离 MySQL 测试验证仅 SELECT 权限下按主键精确定位、主键上限及旧查询兼容。
