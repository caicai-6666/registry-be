# 正式文件目录 API

`catalog` 路由查询 MySQL `resources` 中全部正式文件的基本信息，`resource` 路由用于[按 ID 获取文件原件](resource.md#按-id-获取正式文件原件)。正式资源字段与存储边界见[资源表契约](../architecture/data/resource.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取所有正式文件基本信息](#获取所有正式文件基本信息) | `GET` | `/registry/api/catalog/list` |

---

## 获取所有正式文件基本信息

`GET /registry/api/catalog/list`，读取所有来源平台的正式文件基本信息。

### 认证方式

携带 `Authorization: Bearer <login_code>`，使用[人员登录接口](auth.md#使用密钥登录)取得的免登码。中间件 access_token 不能用于此接口。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer 免登码。

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
    "passport": ["existing-passport", "another-passport"]
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
| `passport` | string[] 或 null | 文件正式入库的最终通行证数组，读取并解码 `resources.passport`；历史未知时为 null。 |

按资源 ID 升序返回，每条只包含上述八个字段；不返回原文件路径、申请备注、审核意见或向量。没有正式文件时返回 `[]`。

`name` 保留入库时提交的文件名称，不用磁盘保存的 `file_url` 或附件 ID 替代，也不从摘要推测名称。

`passport` 是审核通过后的最终通行证数组，不是推荐列表；不同文件可共享通行证，一份文件也可持有多个。保留值、顺序和重复项，不据此合并文件；旧空字符串迁移后返回 `[]`，与未知 null 区分。完整规则见[通行证数组契约](../architecture/data/passport.md)。

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | 免登码缺失、格式错误、无效或过期 | `{"detail":"免登码无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `503` | MySQL 配置、连接或读取失败 | `{"detail":"正式文件查询失败，请稍后重试"}`，含 `Cache-Control: no-store`。 |
| `503` | 应用内资源仓库不可用 | `{"detail":"正式资源仓库不可用"}`，含 `Cache-Control: no-store`。 |

不回显数据库账号、地址、SQL 或异常原文；读取失败不伪装为空列表。

### 请求示例

替换人员免登码；本地直接启动默认端口为 `23000`。

```bash
curl 'http://127.0.0.1:23000/registry/api/catalog/list' \
  -H 'Authorization: Bearer 替换为人员登录免登码'
```

### 行为与边界

MySQL 资源行存在即表示正式入库。SQLite 中的新文件 pending 或拒绝申请不会出现在列表；待删除审核期间原资源仍存在，继续返回；最终正式资源行移除后不再返回。

入库时间取正式记录创建时间 `created_at`；`requested_at` 仍表示原入库申请提交时间，不作为此接口的入库时间。原字段不增加、不修改，不从更新时间或消息消费时间推测入库时间。

只读取数据库基本信息，不读取或渲染原文件、不逐文件检查磁盘存在性、不触发向量生成或智能体。此查询不依赖中间件登录、平台存活或后台消费开关。

应用生命周期管理 `app.state.resource_repository`，与消费者共用仓库和连接池。未启用中间件消费时，首次查询才加载 MySQL 配置并建立连接；结束时由应用关闭，不由路由释放。正式时间按项目 UTC 约定为 MySQL DATETIME 补充时区。

## 验证

```bash
python -m unittest tests.test_catalog_api
```

HTTP 测试使用隔离仓库，应用测试目录位于 `tests/tmp` 并在结束后清理。覆盖八字段响应、入库文件名称、多通行证数组、空数组和历史未知 NULL、人员认证、空列表、错误脱敏、关闭消费后的查询和 OpenAPI。隔离 MySQL 测试使用仅 SELECT 权限账号，验证跨平台、未生成向量的资源及超过 100 条记录均完整返回，入库名称直接取自 `name` 而非磁盘附件名，通行证原值直接返回且重复值不导致文件遗漏，并确认创建时间与申请时间分别处理。
