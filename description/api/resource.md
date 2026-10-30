# 文件原件与头像 API

`resource` 路由负责读取正式存储的文件内容，文件基本信息列表见[正式文件目录 API](catalog.md)。本接口支持 PDF、图片、Office 文档及其他已保存的附件类型。下列路径和示例使用生产前缀与 Compose 端口 `23000`；直接执行 `python app/main.py` 时使用端口 `16000`，并在业务路径前加 `/dev`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [代理读取用户头像](#代理读取用户头像) | `GET` | `/knowledge-accumulation/registry/api/resource/avatar/{filename}` |
| [按 ID 获取正式文件原件](#按-id-获取正式文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/file/{resource_id}` |
| [按审核 ID 获取待入库文件原件](#按审核-id-获取待入库文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/ingestion/{review_id}` |
| [按用户权限获取正式文件原件](#按用户权限获取正式文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/authorized-file/{resource_id}` |
| [可信后台代用户获取正式文件原件](#可信后台代用户获取正式文件原件) | `POST` | `/knowledge-accumulation/registry/api/resource/service-authorized-file/{resource_id}` |

---

## 代理读取用户头像

`GET /knowledge-accumulation/registry/api/resource/avatar/{filename}`，以本次用户 access_token 请求知识后端头像接口，并返回原始 WebP 字节。

**认证方式：** header `Authorization: Bearer <access_token>`，要求登录有效且具有 registry 管理权限。相同用户 token 原样用于上游 Bearer，不使用平台凭据，不创建登录。

**请求参数：** path filename 必填，为 32 位小写十六进制加 .webp，共 37 字符；不能包含目录。无业务 query 参数或请求体，额外 query 不向上游透传。

**成功响应：** `200 image/webp`，原始二进制头像；默认携带 `Cache-Control: private, max-age=86400`、`Vary: Authorization` 和 `X-Content-Type-Options: nosniff`。缓存时长由 `KNOWLEDGE_ACCUMULATION_AVATAR_CACHE_SECONDS` 配置，默认 86400 秒；大于 0 时允许浏览器私有缓存并按用户 token 隔离，公共代理不得缓存。设为 0 时成功响应也使用 `Cache-Control: no-store`，不设置 Vary。不落盘、不转码、不透传上游 Set-Cookie。

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 用户 token 缺失或无效，或上游确认 token 已失效；携带 WWW-Authenticate: Bearer。 |
| `403` | 用户不是 registry 管理员，或上游拒绝读取。 |
| `404` | 上游头像不存在。 |
| `422` | 文件名非法，或上游拒绝参数。 |
| `503` | 认证/管理员依赖异常、知识后端地址未配置、上游故障、超时、重定向、响应类型/大小不合法。 |

错误返回 JSON，携带 `Cache-Control: no-store`，正文脱敏且不透传上游原始错误。上游查询返回 401/403/404/422 时保留对应状态，其他非 200 响应作为依赖故障处理。

**请求示例：**

```bash
curl --fail 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/resource/avatar/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp' \
  -H 'Authorization: Bearer replace-with-user-access-token' \
  --output './avatar.webp'
```

**行为与边界：** 配置及生命周期见[头像代理](../capability/infrastructure/avatar.md)。固定请求知识后端 `/knowledge-accumulation/api/resource/avatar/{filename}`，只转发用户 token，不转发客户端 Cookie、query 或任意头；不跟随重定向。此入口受 registry 管理员边界保护，知识后端直接头像入口仍只要求普通登录。头像更新会生成新文件名，新地址立即获取新内容；浏览器命中有效缓存时不发网络请求，也不会触发中央登录续期。正式文件和待审文件的读取逻辑不变。

---

## 按 ID 获取正式文件原件

`GET /knowledge-accumulation/registry/api/resource/file/{resource_id}`，通过注册中心的正式资源 ID 拉取未经转换的原始文件。

### 认证方式

必须先通过中央用户登录验证，再确认为注册中心当前平台（默认 `registry`）管理员。文件访问等级不能绕过管理员校验。

携带 `Authorization: Bearer <access_token>`，使用知识后端签发的用户 token；可通过[当前身份接口](auth.md#获取当前身份)查询资料。中间件平台 access_token 不能用于此接口。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| path | `resource_id` | integer | 是 | MySQL 正式资源主键，范围 `1` 至 `18446744073709551615`，不是 `source_id` 或消息 ID。 |
| header | `Authorization` | string | 是 | `Bearer <access_token>`。 |

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
| `401` | token缺失、无效或过期 | `access-token 无效或已过期`。 |
| `403` | 不是注册中心管理员 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `422` | ID 不是正整数或超出 MySQL 主键范围 | 参数校验错误列表。 |
| `404` | MySQL 正式资源行不存在 | `正式资源不存在`。 |
| `404` | 资源行存在，但正式目录中原件不存在 | `文件原件不存在`。 |
| `503` | MySQL 配置、连接或查询失败 | `正式文件查询失败，请稍后重试`。 |
| `503` | 应用内仓库或文件服务不可用 | `正式资源仓库不可用` 或 `正式文件读取服务不可用`。 |
| `503` | 文件读取失败、非法路径、符号链接、空文件或超过大小上限 | `文件原件读取失败，请稍后重试`。 |

除认证与参数校验响应外，上述错误均含 `Cache-Control: no-store`。错误不回显数据库配置、真实磁盘路径或异常原文。

### 请求示例

替换资源 ID 与用户 token；示例使用 Compose 后端默认端口 `23000`。保存路径由调用方自行选择，不要求与服务端附件名一致。

```bash
curl --fail 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/resource/file/123' \
  -H 'Authorization: Bearer 替换为当前模式的token' \
  --output './downloaded-file.pdf'
```

### 行为与边界

业务路由组先验证登录与注册中心管理员身份，再通过 `get_stored_resource` 按 `resources.id` 定位正式记录并读取文件。授权失败时不查询正式资源或读取磁盘；中央或管理员查询故障返回503，确认不是管理员返回403。

按正式记录确定所属平台和 `file_url`，从 `RESOURCE_ROOT/<source_platform>/<file_url>` 读取。默认根目录为 `data/resource`，路径规则见[资源表契约](../architecture/data/resource.md)。平台与文件名来自正式记录，不接受调用方传入路径，也不回退到 SQLite 入库暂存或删除保留目录。同名文件在不同平台下通过资源 ID 区分。

复用 `ReviewFileService.read_resource` 和安全的文件目录句柄读取能力，拒绝路径越界、目录和文件符号链接、非普通文件。磁盘读取在线程中执行，沿用 `INGESTION_MAX_FILE_BYTES` 大小上限，默认 `64 MiB`；有界读入内存后返回原字节，不渲染 PDF，不触发模型、不解压、不移动或修改文件。

媒体类型根据后缀推断，不校验文件实际格式。若后缀表示 gzip 等传输编码，则以 `application/octet-stream` 返回压缩原件，不添加 `Content-Encoding` 让客户端自动解压。未知后缀可以正常下载。

读取业务不依赖文件来源平台存活或后台消费开关，但始终要求中央登录及 registry 管理员查询可用，与目录接口共用生命周期管理的 MySQL 仓库。待审核入库文件尚无正式资源 ID，使用下述审核 ID 接口读取；最终删除后，资源移除或正式原件已迁走时返回 `404`。

---

## 按审核 ID 获取待入库文件原件

`GET /knowledge-accumulation/registry/api/resource/ingestion/{review_id}`，拉取 SQLite 待入库申请对应的原始附件。

### 认证方式

必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

携带人员登录取得的 `Authorization: Bearer <access_token>`，中间件令牌不用于此接口。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| path | `review_id` | integer | 是 | SQLite 审核记录 ID，范围 `1` 至 `9223372036854775807`，从[待审列表](ingestion-review.md#获取待入库审核列表)取得。 |
| header | `Authorization` | string | 是 | Bearer 用户 token。 |

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
| `401` | 人员身份无效 | `access-token 无效或已过期`。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `422` | 审核 ID 不合法 | 参数校验错误列表。 |
| `404` | 审核记录不存在 | `入库审核申请不存在`。 |
| `404` | 文件引用或暂存原件不存在 | `待审文件原件不存在`。 |
| `409` | 决定已确定、申请已通过或拒绝 | `该申请已确定审核决定，不能读取待审附件`。 |
| `503` | 审核服务、SQLite 或附件读取失败 | `入库审核服务不可用` 或 `入库审核服务暂不可用，请稍后重试`。 |

错误为 JSON；除认证与参数校验外，均含 `Cache-Control: no-store`，不回显磁盘路径或异常原文。

### 请求示例

替换审核 ID 与用户 token；示例使用 Compose 后端默认端口 `23000`。

```bash
curl --fail 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/resource/ingestion/12' \
  -H 'Authorization: Bearer 替换为当前模式的token' \
  --output './pending-file.pdf'
```

### 行为与边界

待审文件尚未由最终审核确定文件等级，当前不能凭来源平台权限授予读取权，继续要求注册中心管理员。

仅从 SQLite 的 `pending` 且未确定 `decision` 的申请定位附件，读取根目录为 `REVIEW_LOG_ROOT/tmp-file/ingestion`。不查询 MySQL，不回退到 resource 或删除保留目录。读取沿用安全平台名、附件名、目录句柄和文件大小限制，不修改原件。

同一进程内与该申请的审核处理共用锁。审批完成后，待审接口返回 `409`；通过时改用正式 `resource_id` 拉取原件，拒绝附件保留但此接口不提供历史审核附件访问。

---

## 按用户权限获取正式文件原件

`GET /knowledge-accumulation/registry/api/resource/authorized-file/{resource_id}`，供已登录用户按文件访问等级下载正式入库原件，不要求 registry 管理员身份。

### 认证方式

携带知识后端签发的 `Authorization: Bearer <access_token>`。通过中间件 `POST /middleware-service/api/user/login-cache` 验证对应的有效用户并续期，再以可信 userid 查询本地权威员工表的 `platform_permissions`。

### 请求参数

| 位置 | 名称 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| Header | Authorization | string | 是 | `Bearer <access_token>`。 |
| path | resource_id | integer | 是 | MySQL 正式资源主键，范围 1—18446744073709551615，非 source_id 或 SQLite 审核 ID。 |

无 query 参数，无请求体。

### 成功响应

`200 OK`，返回文件原始二进制字节，`Content-Type` 按存储文件名推断，未知类型为 `application/octet-stream`。不转码、不渲染、不返回 JSON。示例响应头：

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="original.pdf"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

### 错误响应

| 状态 | 条件 |
| --- | --- |
| 401 | 用户 token 缺失、无效、过期或被重新登录替换。 |
| 403 | 本地员工不存在、没有文件来源平台键、等级不足或文件等级为 null。 |
| 404 | 正式资源不存在，或授权后找不到正式目录中的原件。 |
| 422 | resource_id 格式或范围不合法。 |
| 503 | 中央认证、员工权限、正式资源查询、存储路径校验或文件读取失败。 |

错误为 `application/json`，如 `{"detail":"无权读取该文件"}`；均禁止缓存，内部路径、SQL 与凭据不回显。

### 请求示例

```bash
curl --fail-with-body \
  'http://127.0.0.1:23000/knowledge-accumulation/registry/api/resource/authorized-file/123' \
  -H 'Authorization: Bearer <替换为用户access_token>' \
  -o original.pdf
```

### 行为与边界

权限判断为：必须显式存在 `platform_permissions[resource.source_platform]`，且该等级大于或等于 `resources.file_level`。显式 0 可以读取等级 0 的文件，缺键不会默认成 0；其他平台的等级与所有管理员关系均不能用于此入口旁路授权。每次请求读取当前 MySQL 权限和文件等级，不采用登录时附带的旧权限副本。

只查询 MySQL 正式资源，并从 `RESOURCE_ROOT/<source_platform>/<file_url>` 安全读取；不读取待审或删除保留目录。原有 `/resource/file/{resource_id}` 仍为管理员下载入口；本接口不开放文件列表、审核记录或其他管理功能。


---

## 可信后台代用户获取正式文件原件

`POST /knowledge-accumulation/registry/api/resource/service-authorized-file/{resource_id}`，供知识后端助理机器人和定时任务在无用户登录 token 时，按任务所属员工当前文件权限读取正式原件。

**认证方式：** header `Authorization: Bearer <knowledge-platform-access-token>`，必须是中间件签发给 `knowledge-accumulation` 的当前平台令牌。registry 使用自身平台令牌调用 `POST /middleware-service/api/platform/token/verify`，只读验证传入令牌并确认平台代号。验证不续期调用方、registry 或用户会话；不使用 heartbeat，不缓存认证结果，不要求用户登录或 registry 管理权。

**请求参数：** path `resource_id` 必填，为正式资源 MySQL ID（正整数，最大18446744073709551615，非 source_id）；无业务 query 参数；JSON body 仅含必填 `userid`：

```json
{"userid":"employee-001"}
```

userid为1—128字符的严格字符串，区分大小写，不能空白或含首尾空白。额外body字段返回422。知识后端必须从可信任务/助理上下文绑定userid，不得让模型或未经验证的外部请求选择他人身份；平台身份和权限不能从body声明。userid本身不构成下载凭据。

**成功响应：** `200`，响应体为未经转换的原件二进制，媒体类型按附件后缀推断，未知或压缩类型为 `application/octet-stream`；携带 `Content-Disposition: attachment; filename="<file_url>"`、`Content-Length`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。例如：

```http
HTTP/1.1 200 OK
Content-Type: application/pdf
Cache-Control: no-store
Content-Disposition: attachment; filename="original.pdf"

<PDF 原始字节>
```

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 调用方平台Bearer缺失、格式错误，或中间件确认目标令牌无效/过期/被替换；用户token不能替代平台token。携带 `WWW-Authenticate: Bearer`。 |
| `403` | 有效调用平台不是 knowledge-accumulation；或员工不存在、缺少文件来源平台授权、等级不足、文件等级未知。 |
| `404` | 正式资源或磁盘原件不存在。 |
| `422` | 资源ID、userid或body字段不合法。 |
| `503` | registry平台会话未就绪、中间件校验不可用或响应非法、员工权限/数据库/文件服务故障。 |

错误正文沿用 `{"detail":"脱敏说明"}`，业务错误携带no-store。只读校验上游401表示registry自身令牌失效，下载返回503并仅失效本次registry令牌，由既有会话任务恢复；不能将其解释为调用方401。中间件403、404（未部署接口）、超时和非法响应均失败关闭，不改用其他认证方式。

**请求示例：**

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/resource/service-authorized-file/1' \
  -H 'Authorization: Bearer replace-with-knowledge-platform-access-token' \
  -H 'Content-Type: application/json' \
  --data '{"userid":"employee-001"}' \
  --output original.pdf
```

**行为与边界：** 平台认证成功后定位正式资源，再从权威MySQL员工行实时读取 `platform_permissions`，只比较资源实际来源平台的等级与当前file_level。显式0与缺键不同，其他平台等级和管理员身份均不能旁路。每次下载重新鉴权，权限撤销、文件等级调整在下一次请求生效；不保证校验完成后正在传输的响应可撤回。授权成功后复用安全原件读取，不读取待审核或删除保留目录，不接收磁盘路径/任意URL，不返回令牌。原有用户Bearer GET入口及管理员入口保留各自鉴权。

依赖中间件部署只读平台校验接口及registry现有平台会话，无新增共享密钥或环境变量。实现与验证见[服务间正式文件授权](../capability/application/authorization.md#服务间正式文件授权)。

---

## 头像存储边界

头像由知识后端存储，直接读取入口为 `GET /knowledge-accumulation/api/resource/avatar/{filename}`，要求有效中央用户登录。registry 保留本文的管理员专用代理，不保存头像、不读取旧 `AVATAR_*` 存储配置。用户列表与平台管理员资料仅返回文件名，`/auth/me` 只返回 userid 和 username。迁移与代理配置见[头像存储边界](../capability/infrastructure/avatar.md)。

---

## 验证

仅在另备本地测试文件的环境执行：

```bash
python -m unittest tests.test_resource_api tests.test_catalog_api tests.test_authorized_resource tests.test_service_authorized_file
```

管理员入口覆盖普通用户拒绝、依赖故障和授权前不读取文件；用户及可信后台入口分别覆盖 token 类型、实时权限、来源平台隔离、显式 0、未知等级及撤权。原件读取覆盖字节一致性、媒体类型、ID 范围、缺失原件、暂存隔离、符号链接、路径及错误脱敏。测试采用隔离替身和 `tests/tmp/`，不连接业务服务、不写 `data/`；真实部署还需验证中间件只读平台令牌接口及网络连通性。
