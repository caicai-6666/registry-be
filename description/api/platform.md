# 平台查看 API

从后台平台目录的内存快照读取全部平台（含注册中心自身）、存活状态、管理员 userid、名称与头像及下次计划同步时间。后台刷新行为见[平台目录刷新服务](../capability/application/platform-directory.md)，存活状态仅表示中间件登录会话有效。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [查看子平台及存活状态](#查看子平台及存活状态) | `GET` | `/knowledge-accumulation/registry/api/platform/list` |
| [查看下次平台同步时间](#查看下次平台同步时间) | `GET` | `/knowledge-accumulation/registry/api/platform/sync-time` |

---

## 查看子平台及存活状态

`GET /knowledge-accumulation/registry/api/platform/list`，返回最近成功同步快照中的全部平台及各自存活状态，包含注册中心自身。

### 认证方式

必须通过中央用户登录验证，并具有注册中心当前平台（默认 `registry`）管理权限；普通用户返回403。文件访问等级不参与放行。

携带 `Authorization: Bearer <access_token>`，使用知识后端签发的用户 token；可通过[当前身份接口](auth.md#获取当前身份)查询资料；不使用中间件 access_token。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer token。

### 成功响应

`200 OK`，媒体类型 `application/json`，`Cache-Control: no-store`：

```json
[
  {
    "code": "contract-pheno",
    "name": "合同平台",
    "description": "合同文件管理",
    "supports_a2a": true,
    "is_sub_management_platform": true,
    "is_management_entry": false,
    "is_alive": true,
    "admins": [
      {"userid": "employee-001", "name": "员工甲", "avatar_url": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp"},
      {"userid": "employee-002", "name": "员工乙", "avatar_url": null}
    ]
  },
  {
    "code": "offline-platform",
    "name": "离线平台",
    "description": "平台当前会话已失活",
    "supports_a2a": false,
    "is_sub_management_platform": false,
    "is_management_entry": false,
    "is_alive": false,
    "admins": []
  }
]
```

每个条目包含下列八个字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | string | 平台代码。 |
| `name` | string | 平台名称。 |
| `description` | string | 平台用途描述。 |
| `supports_a2a` | boolean | 中间件配置声明的是否参与 A2A 协议。 |
| `is_sub_management_platform` | boolean | 中间件配置声明的是否为子管理平台。 |
| `is_management_entry` | boolean | 中间件配置声明的是否作为管理平台入口。 |
| `is_alive` | boolean | 最近同步确认的中间件会话存活状态，可以为 true 或 false。 |
| `admins` | object[] | 管理员资料列表，每项包含 `userid`、`name` 和 `avatar_url`；无管理员时为 `[]`。按员工 userid 排序。 |

三个能力与类型标记随后台平台目录一起刷新，不表示用户权限，不改变注册中心管理员校验；失活平台仍保留其标记。

`admins[].userid` 为管理员的员工唯一标识，可供前端关联用户列表；`admins[].name` 为管理员姓名，历史姓名未知时使用 userid；`admins[].avatar_url` 为头像文件名，无头像时为 null。头像值与登录返回保持一致，不包含目录或域名，使用 `GET /knowledge-accumulation/api/resource/avatar/{filename}` 并携带登录 token 读取，详见[头像迁移说明](../capability/infrastructure/avatar.md)。原字符串数组字段 `admin_names` 已替换为对象数组 `admins`。

快照没有任何平台时返回 `[]`；仅有注册中心自身时仍返回该条目。失活平台仍返回，不包含本地 `last_alive_at`、密钥或令牌。顺序保留中间件列表顺序。

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | token缺失、格式错误、无效或过期 | `{"detail":"access-token 无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `403` | 登录有效但不是注册中心管理员 | `{"detail":"无权管理当前平台"}`。 |
| `503` | 登录状态或管理员资料查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员资料暂不可用`。 |
| `503` | 平台目录尚未同步、同步失败、超过时效或服务已关闭 | `{"detail":"平台目录尚未同步或已过期，请稍后重试"}`，含 `Cache-Control: no-store`。 |
| `503` | 应用内目录服务不可用 | `{"detail":"平台目录服务不可用"}`，含 `Cache-Control: no-store`。 |

### 请求示例

替换登录 token；本地直接启动默认端口为 `23000`，容器内部为 `8000`。

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/platform/list' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

### 行为与边界

完整返回快照中的平台，包含 `registry` 或配置的自身平台代码；不按 `is_alive` 筛选，不修改后台快照，也不人为补造快照中不存在的平台。

每次请求只读取一次平台内存快照，不主动刷新平台状态。快照存在平台时，另通过 MySQL `platform_admins` 联结 `employees` 批量查询管理员姓名与头像引用，避免逐个平台或逐个用户查询；数据库查询失败返回 503，不伪装成无管理员。registry 不再合并本地开发登录配置；资料来自权威员工与管理员关联。姓名相同的不同员工保留各自条目；管理员是否登录不影响展示。每项仅返回 userid、名称与头像文件名，不暴露内部存储路径、用户权限或令牌。中间件管理员列表接口只能查询调用平台自身，故不用于查询各子平台管理员。登录验证仍按现有中央会话规则执行。过期快照不被当成当前平台列表及状态，也不伪装成无平台的空数组；调用方可通过同步时间接口查看下次计划同步时间并重试。目录关闭或中间件尚未就绪时，未成功初始化的列表不可用。

---

## 查看下次平台同步时间

`GET /knowledge-accumulation/registry/api/platform/sync-time`，返回后台下一次计划同步或重试的时间。

### 认证方式

此外必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

携带 `Authorization: Bearer <access_token>`，使用知识后端签发的用户 token；可通过[当前身份接口](auth.md#获取当前身份)查询资料；中间件 access_token 不能用于此接口。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer token。

### 成功响应

`200 OK`，媒体类型 `application/json`，`Cache-Control: no-store`：

```json
{"next_sync_at":"2026-10-14T02:30:30Z"}
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `next_sync_at` | string 或 null | 带时区的 UTC ISO 8601 时间，表示后台实际登记的下一次同步或重试计划。 |

后台正在同步、尚未启动、已禁用或已停止而没有后续计划时返回：

```json
{"next_sync_at":null}
```

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | token缺失、格式错误、无效或过期 | `{"detail":"access-token 无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `503` | 应用内目录服务不可用 | `{"detail":"平台目录服务不可用"}`，含 `Cache-Control: no-store`。 |

### 请求示例

替换 URL 或token后执行：

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/platform/sync-time' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

### 行为与边界

此接口已改为返回 `next_sync_at`，不再返回 `updated_at`。后台在成功同步后按刷新周期登记下一次时间，默认 30 秒；失败或令牌未就绪时按重试周期登记，默认 5 秒。等待结束、开始同步后清除旧计划，结束后登记新计划，停机时清空。

返回的是计划时间，不保证届时同步成功。等待令牌期间，该时间表示下一次检查会话并尝试同步的时刻；客户端查询不会发起同步，也不会改变后台计划。手工调用服务的 `refresh()` 不重置已经登记的后台周期。

目录快照已过期时仍可返回重试计划；此接口的 `200` 不表示当前平台状态可靠。快照的 `updated_at` 仍在内存中记录上次成功同步时间，平台的 `last_alive_at` 仍记录最近确认存活时间，两者不作为此接口的返回字段。
