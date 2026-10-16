# 平台查看 API

从后台平台目录的内存快照读取全部非自身平台及存活状态、下次计划同步时间。后台刷新行为见[平台目录刷新服务](../capability/application/platform-directory.md)，存活状态仅表示中间件登录会话有效。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [查看子平台及存活状态](#查看子平台及存活状态) | `GET` | `/registry/api/platform/list` |
| [查看下次平台同步时间](#查看下次平台同步时间) | `GET` | `/registry/api/platform/sync-time` |

---

## 查看子平台及存活状态

`GET /registry/api/platform/list`，返回最近成功同步快照中的全部子平台及各自存活状态，排除注册中心自身。

### 认证方式

携带 `Authorization: Bearer <login_code>`，使用[人员登录接口](auth.md#使用密钥登录)取得的免登码；不使用中间件 access_token。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer 免登码。

### 成功响应

`200 OK`，媒体类型 `application/json`，`Cache-Control: no-store`：

```json
[
  {
    "code": "contract-pheno",
    "name": "合同平台",
    "description": "合同文件管理",
    "is_alive": true
  },
  {
    "code": "offline-platform",
    "name": "离线平台",
    "description": "平台当前会话已失活",
    "is_alive": false
  }
]
```

每个条目只包含下列四个字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | string | 平台代码。 |
| `name` | string | 平台名称。 |
| `description` | string | 平台用途描述。 |
| `is_alive` | boolean | 最近同步确认的中间件会话存活状态，可以为 true 或 false。 |

没有非自身平台时返回 `[]`。失活平台仍返回，不包含本地 `last_alive_at`、密钥或令牌。顺序保留中间件列表顺序。

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | 免登码缺失、格式错误、无效或过期 | `{"detail":"免登码无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `503` | 平台目录尚未同步、同步失败、超过时效或服务已关闭 | `{"detail":"平台目录尚未同步或已过期，请稍后重试"}`，含 `Cache-Control: no-store`。 |
| `503` | 应用内目录服务不可用 | `{"detail":"平台目录服务不可用"}`，含 `Cache-Control: no-store`。 |

### 请求示例

替换登录免登码；本地直接启动默认端口为 `23000`，容器内部为 `8000`。

```bash
curl 'http://127.0.0.1:23000/registry/api/platform/list' \
  -H 'Authorization: Bearer 替换为人员登录免登码'
```

### 行为与边界

自身平台代码读取 `MIDDLEWARE_PLATFORM_CODE`，默认 `registry`。仅排除自身条目，不按 `is_alive` 筛选，不修改后台快照。

每次请求只读取一次内存快照，不主动调用中间件或刷新状态。过期快照不被当成当前平台列表及状态，也不伪装成无平台的空数组；调用方可通过同步时间接口查看下次计划同步时间并重试。目录关闭或中间件尚未就绪时，未成功初始化的列表不可用。

---

## 查看下次平台同步时间

`GET /registry/api/platform/sync-time`，返回后台下一次计划同步或重试的时间。

### 认证方式

携带 `Authorization: Bearer <login_code>`，使用[人员登录接口](auth.md#使用密钥登录)取得的免登码；中间件 access_token 不能用于此接口。

### 请求参数

无 path 或 query 参数，无请求体。必填 Header：`Authorization`，值为 Bearer 免登码。

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
| `401` | 免登码缺失、格式错误、无效或过期 | `{"detail":"免登码无效或已过期"}`，含 `WWW-Authenticate: Bearer`。 |
| `503` | 应用内目录服务不可用 | `{"detail":"平台目录服务不可用"}`，含 `Cache-Control: no-store`。 |

### 请求示例

替换 URL 或免登码后执行：

```bash
curl 'http://127.0.0.1:23000/registry/api/platform/sync-time' \
  -H 'Authorization: Bearer 替换为人员登录免登码'
```

### 行为与边界

此接口已改为返回 `next_sync_at`，不再返回 `updated_at`。后台在成功同步后按刷新周期登记下一次时间，默认 30 秒；失败或令牌未就绪时按重试周期登记，默认 5 秒。等待结束、开始同步后清除旧计划，结束后登记新计划，停机时清空。

返回的是计划时间，不保证届时同步成功。等待令牌期间，该时间表示下一次检查会话并尝试同步的时刻；客户端查询不会发起同步，也不会改变后台计划。手工调用服务的 `refresh()` 不重置已经登记的后台周期。

目录快照已过期时仍可返回重试计划；此接口的 `200` 不表示当前平台状态可靠。快照的 `updated_at` 仍在内存中记录上次成功同步时间，平台的 `last_alive_at` 仍记录最近确认存活时间，两者不作为此接口的返回字段。
