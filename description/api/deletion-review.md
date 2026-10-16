# 文件删除审核 API

审核员从 SQLite 获取删除申请、按最终审核员查询历史并确定是否删除。删除申请不上传附件，待审原件通过 `resource_id` 使用[正式文件原件接口](resource.md#按-id-获取正式文件原件)。处理阶段与恢复见[人工删除审核服务](../capability/application/deletion-review.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取待删除审核列表](#获取待删除审核列表) | `GET` | `/registry/api/deletion-review/list` |
| [按审核员获取删除审核历史](#按审核员获取删除审核历史) | `GET` | `/registry/api/deletion-review/history` |
| [确定删除最终审核结果](#确定删除最终审核结果) | `POST` | `/registry/api/deletion-review/{review_id}/decision` |

---

## 获取待删除审核列表

`GET /registry/api/deletion-review/list` 返回尚未确定人工决定的删除申请，按审核 ID 升序排列。

**认证：** `Authorization: Bearer <login_code>`，通过[人员登录](auth.md#使用密钥登录)取得。响应禁止缓存。

**请求参数：** 无 path、query 参数，无请求体。

**成功响应：** `200 application/json`，无申请时返回 `[]`。

```json
[
  {
    "id": 7,
    "resource_id": 123,
    "source_platform": "contract-pheno",
    "source_id": "source-1",
    "passport": ["p1", "p2"],
    "name": "设备采购合同",
    "abstract": "设备采购、交付及验收约定。",
    "uploader": "原文件上传人",
    "applicant": "删除申请人",
    "applicant_note": "申请删除",
    "requested_at": "2026-10-14T00:00:00Z"
  }
]
```

`id` 是删除审核 ID；`resource_id` 是待删除的正式资源 ID，用于原件读取。`passport` 是申请提交的原通行证数组；人员、备注及申请时间取自删除申请。历史未知的 `source_id`、`passport`、`applicant` 可以为 null。`name`、`abstract`、`uploader` 是申请时保存的文件业务名称、摘要及原上传人快照，与删除申请人分开；名称不是磁盘文件名。描述直接读取 SQLite，删除后继续保留；历史无法恢复时返回 null，不要求再查询正式文件目录。待审响应不包含最终审核员。

**错误响应：** `401` 表示人员认证失败；`503` 表示 SQLite 或审核服务不可用，响应不会用空列表掩盖存储错误。

**请求示例：** 替换 `REPLACE_LOGIN_CODE`。

```bash
curl -X GET 'http://127.0.0.1:23000/registry/api/deletion-review/list' \
  -H 'Authorization: Bearer REPLACE_LOGIN_CODE'
```

**行为与边界：** 查询仅访问 SQLite，条件为 `status='pending' AND decision IS NULL`。已预留决定、等待存储处理或反馈重试的申请不再次出现；本接口不读取 MySQL、不移动文件，不分页。

---

## 按审核员获取删除审核历史

`GET /registry/api/deletion-review/history` 返回指定人员参与的删除审核历史：此人是删除审核员，或是该文件当初批准入库的注册中心审核员。记录须已完成本地处理，包含批准和拒绝。

**认证：** `Authorization: Bearer <login_code>`。当前无角色分级，已登录人员可以查询其他审核员。

**请求参数：** query `reviewer` 可选，字符串长度 1 至 200，不能全为空白；省略时使用当前登录人员姓名，提供时精确匹配删除审核员或文件入库审核员。无 path 参数，无请求体。

**成功响应：** `200 application/json`，按审核时间倒序、审核 ID 倒序排列，无匹配记录返回 `[]`。

```json
[
  {
    "id": 7,
    "resource_id": 123,
    "source_platform": "contract-pheno",
    "source_id": "source-1",
    "passport": ["p1", "p2"],
    "name": "设备采购合同",
    "abstract": "设备采购、交付及验收约定。",
    "uploader": "原文件上传人",
    "applicant": "删除申请人",
    "applicant_note": "申请删除",
    "requested_at": "2026-10-14T00:00:00Z",
    "status": "approved",
    "reviewer": "审核员姓名",
    "reviewer_note": "## 同意删除",
    "reviewed_at": "2026-10-14T01:00:00Z",
    "result_message_id": "delete-result-001"
  }
]
```

申请字段含义见[待审列表](#获取待删除审核列表)；`status` 为 `approved` 或 `rejected`，表示本地处理结果。响应中的 `reviewer` 始终是实际作出删除决定的注册中心审核员，不替换为查询姓名；入库审核员查询时，两者可能不同，调用方不应再次按响应 reviewer 等于查询姓名过滤。批准删除后 `resource_id` 继续保留为历史关联，MySQL 中对应资源已移除；`name`、`abstract`、`uploader` 仍从删除记录返回，不能依赖原 resource_id 再读取正式文件目录。自动拒绝未绑定资源时，该字段为 null。

`reviewer_note`、`reviewed_at` 的历史未知值保留 null；`result_message_id=null` 表示尚未记录反馈发布凭据或历史未知，不能据此断言中间件未收到消息。未完成本地处理、仍为 pending 的人工决定不进入历史。

**错误响应：** `401` 表示人员认证失败；`422` 表示 reviewer 参数无效；`503` 表示 SQLite 查询或响应数据校验失败。

**请求示例：** 替换免登码及审核员姓名。

```bash
curl -X GET --get 'http://127.0.0.1:23000/registry/api/deletion-review/history' \
  -H 'Authorization: Bearer REPLACE_LOGIN_CODE' \
  --data-urlencode 'reviewer=审核员姓名'
```

**行为与边界：** 只查询 SQLite；正式记录已删除或中间件不可用时仍可查看。筛选条件为删除审核人匹配，或通过 resource_id 关联的已批准入库记录审核人匹配；两个身份同时命中只返回一次。不按申请人或 source_reviewer 匹配，不按可能复用的 source_id 推测原文件身份，不分页。

缺少原入库审核关联或审核员未知的历史数据，只能按已保存的删除审核员查询，不从 MySQL 猜测或补写身份。入库记录未批准时不能作为原文件的入库审核人依据。此扩展不改变[入库审核历史接口](ingestion-review.md#按审核员获取入库审核历史)的筛选规则。

---

## 确定删除最终审核结果

`POST /registry/api/deletion-review/{review_id}/decision` 确定并执行删除审核，完成本地处理后发布中间件反馈。

**认证：** `Authorization: Bearer <login_code>`；审核员姓名从服务端登录身份取得，不接受请求体指定。

**请求参数：**

| 位置 | 参数 | 类型与约束 |
| --- | --- | --- |
| path | `review_id` | 必填，SQLite 删除审核 ID，整数范围 1 至 9223372036854775807；不能使用 resource_id 替代。 |
| body | `approved` | 必填，严格 boolean；true 批准删除，false 拒绝删除。 |
| body | `note` | 可选字符串，默认空字符串，最多 10000 字符，支持 Markdown；拒绝时必须填写非空理由。 |

无 query 参数。请求体媒体类型为 `application/json`，额外字段拒绝接收；不能提交新通行证。

```json
{"approved": true, "note": "## 同意删除"}
```

拒绝示例：

```json
{"approved": false, "note": "文件仍需保留，请勿删除。"}
```

**成功响应：** `200 application/json`，表示本地处理完成且已持久化中间件发布凭据。

```json
{
  "id": 7,
  "status": "approved",
  "approved": true,
  "resource_id": 123,
  "reviewer": "审核员姓名",
  "reviewer_note": "## 同意删除",
  "reviewed_at": "2026-10-14T01:00:00Z",
  "result_message_id": "delete-result-001"
}
```

拒绝时 `status='rejected'`、`approved=false`，正式资源及原件继续保留。`reviewed_at` 是首次预留决定的 UTC 时间，重试不重置。

**错误响应：**

| 状态码 | 触发条件 |
| --- | --- |
| `401` | 人员免登码缺失、无效或已过期。 |
| `404` | 审核记录不存在，或执行批准删除所需的正式原件、恢复保留文件缺失。 |
| `409` | 审核已确定且本次决定、备注或审核员不同；旧结论不允许重审；正式目标缺失、身份或通行证变化。 |
| `422` | ID、请求体类型、备注约束或额外字段无效。 |
| `503` | 中间件会话未就绪，MySQL、SQLite、文件操作或反馈失败；本地可能已完成，不能据此当作未删除。 |

**请求示例：** 替换免登码和路径中的审核 ID。

```bash
curl -X POST 'http://127.0.0.1:23000/registry/api/deletion-review/7/decision' \
  -H 'Authorization: Bearer REPLACE_LOGIN_CODE' \
  -H 'Content-Type: application/json' \
  -d '{"approved":true,"note":"## 同意删除"}'
```

**行为与边界：** 批准时核对原申请绑定的资源 ID、平台、source_id、文件名和通行证全集（忽略顺序与重复项，不接受子集或额外值）；移除 MySQL 正式行，将原件转入 `data/review-log/tmp-file/deletion/<platform>/<file_url>` 保留，完成 SQLite 结论，随后调用 `POST /middleware-service/api/deletion-results/publish`。拒绝不修改 MySQL 或原件。没有自动清理任务。数组规则见[通行证数组契约](../architecture/data/passport.md)。

决定一经持久化，不能修改；同一审核员以相同决定和备注重试，只恢复未完成阶段或返回原结果。反馈失败保留本地结论，启动恢复器持续重试；中间件 409 无法证明旧结果一致，保留未确认状态等待核对。尚不提供多 worker 协调、保留文件下载或撤销已确定决定的接口。
