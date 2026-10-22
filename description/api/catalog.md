# 正式文件目录 API

`catalog` 路由查询 MySQL `resources` 中全部正式文件的基本信息，`resource` 路由用于[按 ID 获取文件原件](resource.md#按-id-获取正式文件原件)。正式资源字段与存储边界见[资源表契约](../architecture/data/resource.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取文件等级调整历史](#获取文件等级调整历史) | `GET` | `/knowledge-accumulation/registry/api/catalog/file-level-history` |
| [调整正式文件等级](#调整正式文件等级) | `POST` | `/knowledge-accumulation/registry/api/catalog/adjust-file-level` |
| [获取所有正式文件基本信息](#获取所有正式文件基本信息) | `GET` | `/knowledge-accumulation/registry/api/catalog/list` |

---

## 获取所有正式文件基本信息

`GET /knowledge-accumulation/registry/api/catalog/list`，读取所有来源平台的正式文件基本信息。

### 认证方式

此外必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

携带 `Authorization: Bearer <access_token>`，使用知识后端签发的用户 token；可通过[当前身份接口](auth.md#获取当前身份)查询资料。中间件 access_token 不能用于此接口。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer token。

当前返回全部记录，不分页，不按平台、通行证或向量生成状态过滤。

### 成功响应

`200 OK`，媒体类型 `application/json`，`Cache-Control: no-store`：

```json
[
  {
    "id": 123,
    "source_platform": "contract-pheno",
    "name": "设备采购合同.pdf",
    "applicant": "张三",
    "reviewer": "注册中心审核员",
    "ingested_at": "2026-10-14T02:30:00Z",
    "abstract": "设备采购及交付合同摘要",
    "passport": ["existing-passport", "another-passport"],
    "file_level": 2
  }
]
```

| 字段 | 类型 | 来源与含义 |
| --- | --- | --- |
| `id` | integer | 文件在注册中心的 MySQL 正式资源 ID。 |
| `source_platform` | string | 文件来源平台代码。 |
| `name` | string 或 null | 入库申请中的文件名称，直接读取 `resources.name`；历史未知时为 null。 |
| `applicant` | string 或 null | 入库申请人，历史未知时为 null。 |
| `reviewer` | string 或 null | 批准入库的注册中心审核人，历史未知时为 null。 |
| `ingested_at` | string | 正式资源 `created_at`，带时区的 UTC ISO 8601 时间，可包含微秒。 |
| `abstract` | string 或 null | 文件摘要，历史未知时为 null。 |
| `file_level` | integer 或 null | 文件当前等级（初次由入库审核确定，后续可由管理员调整）；历史未知为 null。 |
| `passport` | string[] 或 null | 文件正式入库的最终通行证数组，读取并解码 `resources.passport`；历史未知时为 null。 |

`file_level` 为文件当前等级（初次由入库审核确定，后续可由管理员调整），严格整数，历史未知为 null；文件等级与管理平台列表独立。

按资源 ID 升序返回，每条只包含上述九个字段；不返回原文件路径、申请备注、审核意见或向量。没有正式文件时返回 `[]`。

`name` 保留入库时提交的文件名称，不用磁盘保存的 `file_url` 或附件 ID 替代，也不从摘要推测名称。

`passport` 是审核通过后的最终通行证数组，不是推荐列表；不同文件可共享通行证，一份文件也可持有多个。保留值、顺序和重复项，不据此合并文件；旧空字符串迁移后返回 `[]`，与未知 null 区分。完整规则见[通行证数组契约](../architecture/data/passport.md)。

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | token缺失、格式错误、无效或过期 | `{"detail":"access-token 无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `503` | MySQL 配置、连接或读取失败 | `{"detail":"正式文件查询失败，请稍后重试"}`，含 `Cache-Control: no-store`。 |
| `503` | 应用内资源仓库不可用 | `{"detail":"正式资源仓库不可用"}`，含 `Cache-Control: no-store`。 |

不回显数据库账号、地址、SQL 或异常原文；读取失败不伪装为空列表。

### 请求示例

替换用户 token；本地直接启动默认端口为 `23000`。

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/catalog/list' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

### 行为与边界

MySQL 资源行存在即表示正式入库。SQLite 中的新文件 pending 或拒绝申请不会出现在列表；待删除审核期间原资源仍存在，继续返回；最终正式资源行移除后不再返回。

入库时间取正式记录创建时间 `created_at`；`requested_at` 仍表示原入库申请提交时间，不作为此接口的入库时间。原字段不增加、不修改，不从更新时间或消息消费时间推测入库时间。

只读取数据库基本信息，不读取或渲染原文件、不逐文件检查磁盘存在性、不触发向量生成或智能体。查询业务逻辑不依赖平台存活或后台消费开关；生产登录与管理员校验仍需中间件可用。

应用生命周期管理 `app.state.resource_repository`，与消费者共用仓库和连接池。未启用中间件消费时，首次查询才加载 MySQL 配置并建立连接；结束时由应用关闭，不由路由释放。正式时间按项目 UTC 约定为 MySQL DATETIME 补充时区。

## 验证

```bash
python -m unittest tests.test_catalog_api
```

HTTP 测试使用隔离仓库，应用测试目录位于 `tests/tmp` 并在结束后清理。覆盖九字段响应、入库文件名称、多通行证数组、空数组和历史未知 NULL、人员认证、空列表、错误脱敏、关闭消费后的查询和 OpenAPI。隔离 MySQL 测试使用仅 SELECT 权限账号，验证跨平台、未生成向量的资源及超过 100 条记录均完整返回，入库名称直接取自 `name` 而非磁盘附件名，通行证原值直接返回且重复值不导致文件遗漏，并确认创建时间与申请时间分别处理。

---

## 调整正式文件等级

`POST /knowledge-accumulation/registry/api/catalog/adjust-file-level`

### 认证方式

`Authorization: Bearer <access_token>`，要求用户中央登录有效且具有 registry 管理权限。操作人由服务器取得，不接受客户端指定。

### 请求参数

无 path 或 query 参数。请求体为 JSON，不接受额外字段。

| 字段 | 类型与约束 |
| --- | --- |
| `resource_id` | 必填，注册中心正式文件 ID，严格整数，1 至 18446744073709551615。 |
| `file_level` | 必填，调整后的等级，严格有符号 64 位整数；不接受字符串、布尔值、小数或 null。 |
| `note` | 可选，调整备注，默认空字符串，最多 10000 字符。 |

### 成功响应

`200 application/json`，`Cache-Control: no-store`：

```json
{
  "id": 1,
  "resource_id": 123,
  "previous_file_level": 2,
  "file_level": 5,
  "reviewer": "employee-001",
  "note": "重新定级",
  "status": "published",
  "result_message_id": "adjustment-001",
  "created_at": "2026-10-21T08:00:00Z"
}
```

`id` 是 SQLite 调整日志 ID；`reviewer` 是操作人的 userid；`previous_file_level=null` 表示原等级未知。200 表示 MySQL 更新和中间件流发布均已确认，不表示来源平台已经消费。

### 错误响应

| 状态码 | 含义 |
| --- | --- |
| `401` / `403` | 用户未登录或没有注册中心管理权限。 |
| `404` | 正式文件不存在。 |
| `409` | 文件缺少来源 ID、等级无变化、存在另一项未完成调整，或重试时资源身份/等级已变化。 |
| `422` | 请求字段缺失、非法或存在额外字段。 |
| `503` | 仓库、日志或中间件不可用，或发布尚未确认。 |

业务错误包含 `detail={message,log_id,database_updated}`。`log_id` 可为空；`database_updated=true` 表示本地提交已确认，null 表示结果不确定；不得将 503 简单视为本地未更新。认证或服务缺失错误沿用现有字符串 detail。重复提交相同文件、等级、备注且由原操作人执行，可继续未完成日志。中间件 409 对外返回未完成的 503，不据此伪造成功凭据。

### 请求示例

```bash
curl -X POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/catalog/adjust-file-level' \
  -H 'Authorization: Bearer 替换为用户token' \
  -H 'Content-Type: application/json' \
  --data '{"resource_id":123,"file_level":5,"note":"重新定级"}'
```

### 行为与边界

等级修改适用于整份正式文件，不改变通行证或用户权限。不移动原文件、不重新生成向量。日志先保留意图，然后按本地提交、中间件发布、日志确认的顺序完成；旧入库/删除审核快照不改写。失败恢复、单实例边界和跨系统确认限制见[文件等级调整](../capability/application/file-level-adjustment.md)。


---

## 获取文件等级调整历史

`GET /knowledge-accumulation/registry/api/catalog/file-level-history`

### 认证方式

携带 `Authorization: Bearer <access_token>`。所有中央登录有效且具有 registry 管理权限的用户均可查看全部操作人的记录，不限于本人发起的调整；普通登录用户不可访问。

### 请求参数

无 path、query 参数，无请求体。不分页、不按操作人筛选，按日志创建时间倒序、日志 ID 倒序返回。

### 成功响应

`200 application/json`，`Cache-Control: no-store`；没有记录时返回 `[]`。

```json
[
  {
    "id": 1,
    "resource_id": 123,
    "source_platform": "contract-pheno",
    "source_id": "source-001",
    "previous_file_level": 2,
    "file_level": 5,
    "reviewer": "employee-001",
    "reviewer_name": "张三",
    "note": "重新定级",
    "status": "published",
    "result_message_id": "adjustment-001",
    "created_at": "2026-10-21T08:00:00Z",
    "updated_at": "2026-10-21T08:00:01Z"
  }
]
```

`resource_id` 是注册中心原正式文件 ID，文件删除后保留；`source_id` 是来源平台文件 ID。`previous_file_level` 可为 null，表示调整前未知。`reviewer` 是操作人 userid，`reviewer_name` 是操作时的姓名快照。`note` 是调整备注，时间均为带时区的 UTC 时间。

`file_level` 是该次调整的目标等级，不代表文件当前等级；当前等级从 catalog/list 获取。记录包含以下全部状态：

| `status` | 含义 |
| --- | --- |
| `prepared` | 已记录调整意图，本地提交未确认；不能断定 MySQL 未更新。 |
| `local_updated` | MySQL 更新已确认，中间件发布尚未确认。 |
| `published` | MySQL 更新及中间件发布均已确认，不表示来源平台已消费。 |

`result_message_id` 未确认时为 null；不能仅凭空值断定消息从未发布。接口不返回内部错误、磁盘文件名或凭据。

### 错误响应

| 状态码 | 含义 |
| --- | --- |
| `401` | 用户未登录、token 无效或过期。 |
| `403` | 用户没有 registry 管理权限。 |
| `503` | 中央认证、管理员查询、SQLite、日志服务或响应数据不可用；响应禁止缓存，不伪装为空数组。 |

### 请求示例

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/catalog/file-level-history' \
  -H 'Authorization: Bearer 替换为用户token'
```

### 行为与边界

业务查询只读取 SQLite 快照，不读取 MySQL、原文件或发起消息发布；认证仍需中间件可用。已删除文件的日志同样返回，不改写日志状态或触发重试。数据定义见[文件等级调整日志](../architecture/data/file-level-adjustment.md)。
