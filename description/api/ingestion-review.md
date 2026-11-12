# 文件入库审核 API

审核员从 SQLite 拉取待审申请、查询审核历史并提交最终决定。原件读取见[待入库文件原件接口](resource.md#按审核-id-获取待入库文件原件)，处理阶段及恢复见[人工入库审核服务](../capability/application/ingestion-review.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取待入库审核列表](#获取待入库审核列表) | `GET` | `/knowledge-accumulation/registry/api/ingestion-review/list` |
| [按审核员获取入库审核历史](#按审核员获取入库审核历史) | `GET` | `/knowledge-accumulation/registry/api/ingestion-review/history` |
| [重新推荐并保存通行证](#重新推荐并保存通行证) | `POST` | `/knowledge-accumulation/registry/api/ingestion-review/{review_id}/recommendations` |
| [确定入库最终审核结果](#确定入库最终审核结果) | `POST` | `/knowledge-accumulation/registry/api/ingestion-review/{review_id}/decision` |

---

## 获取待入库审核列表

`GET /knowledge-accumulation/registry/api/ingestion-review/list`，读取尚未确定人工决定的入库申请。

### 认证方式

此外必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

使用知识后端签发的 `Authorization: Bearer <access_token>`；[当前身份接口](auth.md#获取当前身份)只查询已有登录。中间件令牌不用于此接口。

### 请求参数

必填 Header 为 `Authorization`，无 path、query 参数，无请求体。当前返回全部待审申请，不分页。

### 成功响应

`200 OK`，`application/json`，`Cache-Control: no-store`：

```json
[
  {
    "id": 12,
    "source_platform": "contract-pheno",
    "source_id": "contract-001",
    "name": "设备采购合同.pdf",
    "abstract": "设备采购及交付合同摘要",
    "applicant": "张三",
    "applicant_note": "请审核入库",
    "requested_at": "2026-10-14T02:30:00Z",
    "passport_recommendations": [
      {"passport": "existing-passport", "reason": "属于同一采购事项"}
    ]
  }
]
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | integer | SQLite 入库审核记录 ID，用于提交决定和读取待审原件。 |
| `source_platform` | string | 来源平台代码。 |
| `source_id` | string 或 null | 来源平台内的文件 ID。 |
| `name`、`abstract` | string 或 null | 入库时的文件名称与摘要。 |
| `applicant` | string 或 null | 来源平台发起入库申请的人，当前由上游消息的 `reviewer` 映射；历史明确的申请人值优先保留。 |
| `applicant_note` | string | 申请人备注，可为空字符串。 |
| `requested_at` | string | 带时区的 UTC 申请时间。 |
| `passport_recommendations` | array 或 null | `{passport, reason}` 推荐条目；`[]` 表示无有效推荐，可能是无候选、均不推荐或全部组判断失败；null 表示历史申请未推荐。失败详情不在当前接口返回。 |

没有待审申请时返回 `[]`。不返回摘要向量、磁盘路径或数据库连接信息。

待审列表只返回申请人 `applicant`，不返回 `source_reviewer` 或注册中心最终审核人 `reviewer`。上游把发起人命名为 `reviewer`，该名称不表示此申请已在注册中心完成审核。

### 错误响应

| 状态码 | detail | 条件 |
| --- | --- | --- |
| `401` | `access-token 无效或已过期` | 人员身份无效。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `503` | `入库审核服务不可用` | 应用没有审核服务。 |
| `503` | `入库审核服务暂不可用，请稍后重试` | SQLite 查询或数据契约校验失败。 |

### 请求示例

替换用户 token，本地默认端口为 `23000`。

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/list' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

### 行为与边界

仅返回 `status='pending' AND decision IS NULL` 的申请，按审核 ID 升序排列。决定已确定但尚在处理、通过和拒绝申请均不返回。列表数据只查询 SQLite，不依赖 MySQL、平台存活或消费开关；生产登录和管理员验证仍需中间件可用。

审核记录 `id` 与正式资源 `resource_id` 是两种身份，不能混用。原始摘要与推荐结果已由后台工作流生成，本接口不重新调用智能体。

---

## 按审核员获取入库审核历史

`GET /knowledge-accumulation/registry/api/ingestion-review/history`，按注册中心最终审核员标识查询本地已经完成的入库审核记录。

### 认证方式

此外必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

携带人员登录取得的 `Authorization: Bearer <access_token>`。省略查询参数时使用当前登录审核员标识；具有注册中心管理权限的人员可以指定其他审核员标识查询。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| header | `Authorization` | string | 是 | Bearer 用户 token。 |
| query | `reviewer` | string | 否 | 注册中心最终审核员标识，精确匹配；1 至 200 字符，不能仅为空白。省略时查询当前登录审核员。 |

无 path 参数，无请求体。当前返回全部匹配历史，不分页。

### 成功响应

`200 OK`，`application/json`，`Cache-Control: no-store`：

```json
[
  {
    "id": 12,
    "source_platform": "contract-pheno",
    "source_id": "contract-001",
    "name": "设备采购合同.pdf",
    "abstract": "设备采购及交付合同摘要",
    "applicant": "张三",
    "applicant_note": "请审核入库",
    "requested_at": "2026-10-14T02:30:00Z",
    "passport_recommendations": [],
    "resource_id": 123,
    "status": "approved",
    "passport": ["existing-passport", "another-passport"],
    "file_level": 2,
    "reviewer": "注册中心审核员",
    "reviewer_note": "同意入库",
    "reviewed_at": "2026-10-14T03:00:00Z",
    "result_message_id": "feedback-001"
  }
]
```

申请快照字段与[待审列表](#获取待入库审核列表)一致，此外包含：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `resource_id` | integer 或 null | 关联的 MySQL 正式资源 ID；未创建资源时为 null。 |
| `status` | string | `approved` 或 `rejected`，表示本地存储处理已完成。 |
| `file_level` | integer 或 null | 已冻结的文件等级，拒绝或历史未知为 null。 |
| `passport` | string[] 或 null | 最终通行证数组，拒绝为 `[]`，历史未知时为 null。 |
| `reviewer` | string | 作出最终决定的注册中心审核员。 |
| `reviewer_note` | string 或 null | 最终审核意见，支持 Markdown，历史未知时为 null。 |
| `reviewed_at` | string 或 null | UTC 审核时间，历史未知时为 null。 |
| `result_message_id` | string 或 null | 中间件反馈消息 ID；未发布或历史未知时为 null。 |

没有匹配记录时返回 `[]`。不返回摘要向量、原件字节、磁盘路径或服务器凭据。

### 错误响应

| 状态码 | 条件 | detail |
| --- | --- | --- |
| `401` | 用户 token缺失、无效或过期 | `access-token 无效或已过期`。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `422` | 审核员标识为空、纯空白或超过长度限制 | 参数校验错误列表。 |
| `503` | 审核服务未装配 | `入库审核服务不可用`。 |
| `503` | SQLite 查询或历史数据校验失败 | `入库审核服务暂不可用，请稍后重试`。 |

存储错误不回显路径或异常原文，不以空列表伪装查询失败。

### 请求示例

替换用户 token，本地默认端口为 `23000`。省略参数时查询自己的历史：

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/history' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

指定审核员时对标识进行 URL 编码：

```bash
curl --get 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/history' \
  -H 'Authorization: Bearer 替换为当前模式的token' \
  --data-urlencode 'reviewer=注册中心审核员'
```

### 行为与边界

只查询 SQLite 的注册中心最终 `reviewer`，不按申请人 `applicant` 匹配，也不执行模糊标识搜索。返回 `approved` 和 `rejected` 两种状态，排除尚未处理完的 `pending`，包括已经预留人工决定但尚未完成本地存储的记录。

按审核时间倒序，同一时间按审核 ID 倒序；历史审核时间为空的记录排在最后。反馈尚未发布但本地处理已完成的记录仍返回，`result_message_id` 为 null；该值也可能表示历史凭据缺失，不能据此认定旧记录尚未发送。

不查询 MySQL、不读取原件、不触发中间件发布或恢复操作。正式资源后来删除也不影响审核历史。申请人为空但 SQLite 保留了上游发起人时，返回映射后的 `applicant`；不再额外返回 `source_reviewer`。旧记录缺少文件等级、通行证、审核时间或推荐列表时保留 null；不从其他存储推测或补写。按“注册中心自动审核员”查询时可返回自动拒绝历史。

---

## 重新推荐并保存通行证

`POST /knowledge-accumulation/registry/api/ingestion-review/{review_id}/recommendations`，针对尚未确定人工决定的待审申请，重新检索当前正式资源、运行通行证推荐并替换已保存的推荐列表。

### 认证方式

使用知识后端签发的 `Authorization: Bearer <access_token>`，并要求当前用户具有注册中心管理员权限。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| header | `Authorization` | string | 是 | Bearer 用户 token。 |
| path | `review_id` | integer | 是 | SQLite 入库审核 ID，范围 `1` 至 `9223372036854775807`，不是正式资源 ID。 |

无 query 参数，无请求体。后端读取待审记录的名称、摘要、已有摘要向量和暂存原件，前端不提交或覆盖这些输入。

### 成功响应

`200 OK`，`application/json`，`Cache-Control: no-store`。仅在推荐列表已保存后返回：

```json
{
  "id": 12,
  "passport_recommendations": [
    {"passport": "existing-passport", "reason": "属于同一采购事项"}
  ]
}
```

`id` 为审核记录 ID；`passport_recommendations` 是本次完整替换后的推荐数组，条目与[待审列表](#获取待入库审核列表)一致。刷新待审列表可读到同一结果。空数组同样会保存，可能表示无候选、均不推荐或全部组判断失败；失败详情不在接口中返回。

### 错误响应

| 状态码 | 条件 | detail |
| --- | --- | --- |
| `401` | 未登录或 token 无效 | `access-token 无效或已过期`。 |
| `403` | 当前用户不是注册中心管理员 | `无权管理当前平台`。 |
| `404` | 审核记录或暂存原件不存在 | `入库审核申请不存在` 或 `待审文件原件不存在`。 |
| `409` | 已确定决定或已完成审核 | `该申请已确定审核决定，不能重新推荐`。 |
| `409` | 同一申请已有推荐正在执行 | `该申请正在推荐中，请勿重复发起`。 |
| `409` | 历史记录缺少必要输入 | `申请缺少名称、摘要、向量或原件信息，不能重新推荐`。 |
| `409` | 推理期间申请已被审核或更新 | `申请已审核或发生变更，请刷新后重试`。 |
| `422` | 审核 ID 不符合约束 | 参数校验错误列表。 |
| `503` | 中央认证、管理员或审核依赖不可用 | 与待审列表相同的依赖错误。 |
| `503` | 推荐配置不可用 | `通行证推荐服务未配置，请检查模型与搜索配置`。 |
| `503` | 检索、公共 PDF 准备、输入校验或存储失败 | `入库审核服务暂不可用，请稍后重试`。 |

普通组级模型失败沿用推荐子图容错，返回有效推荐或空数组，不直接产生 `503`。检索与公共处理失败不会覆盖旧推荐；并发冲突不会覆盖其他请求已更新的结果。

### 请求示例

替换审核 ID 与用户 token；部署使用 `/dev` 前缀时，在完整路径前补上 `/dev`：

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/12/recommendations' \
  -H 'Authorization: Bearer 替换为当前模式的token'
```

### 行为与边界

这是等待计算和保存完成的同步 HTTP 接口，没有后台任务 ID 或轮询接口。前端点击后显示进行中并禁用重复点击，收到成功响应后更新推荐区域；不自动改动审核员已选择的最终通行证。推荐期间允许提交审核，迟到的推荐结果会被丢弃并返回 `409`。

模型调用可能持续数分钟，前端及反向代理需要为该请求设置足够的等待时间。网络中断或网关超时不能证明后端已取消或未保存，应先刷新列表；若重试收到“正在推荐”，等待后再查看结果。进程重启不恢复未完成的手动推荐。

本接口只更新推荐列表和记录更新时间，不重新判重、不创建正式资源、不生成最终通行证、不触发 ACK 或审核反馈。消费开关关闭时仍可使用，前提是模型、embedding、搜索配置及 MySQL 正常。历史缺失向量等必要输入不自动补齐。

---

## 确定入库最终审核结果

`POST /knowledge-accumulation/registry/api/ingestion-review/{review_id}/decision`，确定同意或拒绝入库，并完成存储及中间件反馈。

### 认证方式

此外必须具有注册中心当前平台（默认 `registry`）的管理权限，文件访问等级不能替代管理权限。

携带 `Authorization: Bearer <access_token>`。审核人标识由当前登录身份取得，请求体不能提供或覆盖 `reviewer`。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| path | `review_id` | integer | 是 | SQLite 审核 ID，范围 `1` 至 `9223372036854775807`。 |
| header | `Authorization` | string | 是 | Bearer 用户 token。 |
| body | `approved` | boolean | 是 | 必须是 JSON boolean，true 表示同意，false 表示拒绝。 |
| body | `file_level` | integer 或 null | 批准时必填 | 严格的有符号 64 位整数；拒绝时省略或 null。不接受 bool、字符串或小数，等级含义尚未定义。 |
| body | `note` | string | 否 | 默认 `""`，最多 10000 字符，支持 Markdown；拒绝时必须非空且非纯空白。 |
| body | `passport` | string[] 或 null | 否 | 通过时可选择多个已有或自定义通行证，数组非空，每项非空白且最多 512 字符；省略或 null 时生成 UUID 单元素数组。拒绝时只能省略、null 或 `[]`。 |
| body | `generate_new_passport` | boolean | 否 | 默认 `false`。批准时为 `true` 会由后端生成一个新的 UUID 通行证并追加到 `passport`；`passport` 为空数组时只生成新通行证。拒绝时必须为 `false`。 |

无 query 参数。推荐列表提供选择建议，不限制审核员只能选择推荐条目。通行证允许被多个资源共用，一份文件也可持有多个通行证；新人工提交不再接受单字符串。

### 成功响应

`200 OK`，`application/json`，`Cache-Control: no-store`。仅在正式存储处理和中间件发布回执均已保存后返回成功：

```json
{
  "id": 12,
  "status": "approved",
  "resource_id": 123,
  "passport": ["existing-passport", "another-passport"],
  "file_level": 2,
  "reviewer": "注册中心审核员",
  "reviewer_note": "## 审核意见\n\n同意入库。",
  "reviewed_at": "2026-10-14T03:00:00Z",
  "result_message_id": "feedback-001"
}
```

拒绝时 `status="rejected"`、`resource_id=null`、`passport=[]`、`file_level=null`，其余字段含义一致。`reviewed_at` 是决定首次确定时间；恢复和重复提交不刷新审核时间。`result_message_id` 是反馈消息 ID，不是申请消息 ID。

### 错误响应

| 状态码 | 条件 | detail |
| --- | --- | --- |
| `401` | 人员身份无效 | `access-token 无效或已过期`。 |
| `403` | 已登录但没有当前平台管理权限 | `无权管理当前平台`，响应禁止缓存。 |
| `503` | 登录状态或管理员名单查询不可用 | `中间件用户认证暂不可用` 或 `平台管理员服务暂不可用`。 |
| `422` | ID、boolean、意见、通行证、缺失批准等级或等级类型/范围、额外字段不合法 | 参数校验错误列表。 |
| `404` | 审核记录不存在 | `入库审核申请不存在`。 |
| `404` | 新批准决定所需的待审原件不存在 | `待审文件原件不存在`。 |
| `409` | 已完成审核或决定、通行证、文件等级、意见、审核人不一致 | `该申请已经完成审核` 或 `入库审核决定已确定，不能更改`。 |
| `409` | 原件信息缺失或正式文件身份已被其他申请占用 | `申请缺少原件信息，不能批准入库` 或 `同平台文件或源 ID 已对应其他正式资源`。 |
| `503` | 中间件会话尚未就绪 | `中间件会话尚未就绪，请稍后重试`。 |
| `503` | SQLite、MySQL、文件迁移或中间件发布失败 | `入库审核服务暂不可用，请稍后重试`。 |

除认证和参数校验外，错误含 `Cache-Control: no-store`。存储及网络错误不回显账号、地址、路径或上游响应。

### 请求示例

替换审核 ID 与用户 token；通过时选择已有通行证：

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/12/decision' \
  -H 'Authorization: Bearer 替换为当前模式的token' \
  -H 'Content-Type: application/json' \
  --data '{"approved":true,"file_level":2,"passport":["existing-passport"],"generate_new_passport":true,"note":"同意入库并新建通行证"}'
```

拒绝入库：

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/ingestion-review/12/decision' \
  -H 'Authorization: Bearer 替换为当前模式的token' \
  -H 'Content-Type: application/json' \
  --data '{"approved":false,"note":"材料不完整，请补充后重新申请"}'
```

### 行为与边界

通过后将原件从入库暂存目录移至正式资源目录，复制 SQLite 中的文件元信息、摘要向量、申请人和备注、申请时间及本次审核人、意见、通行证和文件等级至 MySQL，回填 `resource_id` 并发布反馈。拒绝只更新 SQLite 并反馈空通行证，不写入 MySQL，原件保留在暂存目录，暂不清理。

初次提交要求有效的中间件平台会话。决定保存后不得更改；同一审核人按相同决定、意见、通行证、生成信号和文件等级重试会恢复处理或返回原结果。`generate_new_passport=true` 只在首次预留决定时生成一次；重复请求复用数据库中已冻结的最终数组，不重复追加。省略通行证且生成信号为 false 时，继续兼容生成一个新通行证。其他审核人不能覆盖或接管该决定。已完成的重复提交不再次创建资源、迁移文件或发布反馈。

`503` 可能发生在决定已经保存或文件正式入库之后，不代表全部回滚。后台会恢复未完成阶段；调用方也可用原请求重试，不能更改决定。正式身份冲突在文件晋升前检出且 MySQL 未提交时，释放批准预留并返回待审，可继续拒绝该申请。

审核结果和历史记录返回 `file_level`；批准为审核员确定的整数，拒绝或历史未知为 null。等级适用于整份文件及其全部通行证，不推导管理员身份或自动定义文件访问等级比较规则。

中间件反馈继续使用原申请的 `message_id` 作为 `request_id`，目标平台取自 SQLite，`reviewer` 为实际审核人；非空通行证数组表示通过，空数组表示拒绝。所有新反馈都携带 `file_level`，批准发送已冻结的等级，拒绝固定发送 0（不代表生效等级）。数组保留值、顺序和重复项，见[通行证数组契约](../architecture/data/passport.md)。中间件 `409` 没有旧反馈正文，不能直接视为已成功发布，仍返回 `503` 并保留未完成凭据，需核对上游反馈。

本接口处理入库审核；删除审核使用独立的[删除审核 API](deletion-review.md)。


历史未发布批准记录若没有文件等级，恢复器不会猜测或自动发布，需先核对申请与正式资源并显式补齐两处等级；已发布历史结果不自动重新发布。
