# 注册中心审核表契约

> **当前阶段：** 入库接收、删除接收、自动拒绝及两类人工审核已接入 SQLite；批准入库后正式写入 MySQL，批准删除后移除正式资源并保留原件。

本文维护审核申请的数据定义。连接、事务和配置见[SQLite 基础设施](../../capability/infrastructure/sqlite.md)，正式文件的数据定义见[资源表契约](resource.md)。建表执行来源为 [sqlite_schema.sql](../../../app/infrastructure/sqlite_schema.sql)。

---

## 存储边界与关联

`ingestion_reviews` 暂存新文件的完整入库申请及处理结果，`deletion_reviews` 保存已有资源的删除申请、文件描述快照及审核信息。两张表按各自业务分别定义，不使用统一字段模板。每张表对 `message_id` 建立唯一约束，供消息重投去重。

| 场景 | SQLite | MySQL |
| --- | --- | --- |
| 入库申请待审核 | 完整申请，状态 `pending`，`resource_id=NULL`。 | 不创建资源、不更新资源。 |
| 入库申请拒绝 | 审核结论与理由，状态 `rejected`。 | 不创建资源。 |
| 入库审核通过并正式入库 | 状态 `approved`，回填资源 ID。 | 创建正式资源，不写入审核状态。 |
| 删除申请待审核 | 申请信息，状态 `pending`，关联已有资源 ID。 | 不修改正式资源。 |
| 删除申请拒绝 | 状态 `rejected`，保存理由。 | 不修改正式资源。 |
| 删除申请通过 | 保存最终审核结论。 | 由最终审核处理正式资源删除。 |

当前申请与人工审核所需数据从 SQLite 获取；判重候选等已入库资源仍从 MySQL 只读检索，不能把当前待审核申请提前写入正式资源表。

每张审核表对非空 `resource_id` 建立唯一索引，分别与 `resources.id` 一一对应；待入库与未创建资源的拒绝记录允许 NULL。审核记录自身的 `id` 是 SQLite 主键，与 MySQL 资源 ID 含义不同。每种审核暂时只保留同一资源的一条记录，不维护多次删除申请历史。

跨数据库关联由应用负责，不声明跨 MySQL/SQLite 外键。

---

## 公共字段

| 字段 | 含义 |
| --- | --- |
| `id` | SQLite 自增审核记录主键。 |
| `message_id` | 中间件申请标识，每张表内唯一，长度 1 至 128。 |
| `resource_id` | 正式 MySQL 资源 ID，可空且非空时唯一；十进制字符串兼容无符号 BIGINT。 |
| `request_offset` | 首次消费位置，可空，保存为十进制字符串。 |
| `result_message_id` | 已确认发布的反馈消息 ID，可空；自动拒绝分支凭此恢复 ACK，人工审核凭此恢复发布。 |
| `decision` | 人工决定预留值：NULL、`approved` 或 `rejected`；自动拒绝及升级前历史结论保持 NULL，不由人工恢复器重新发布。 |
| `source_platform`、`source_id` | 来源平台与源文件 ID，后者允许历史缺失。 |
| `applicant` | 本次申请人。入库时当前上游 `reviewer` 就是发起入库的人，映射到此字段；历史显式申请人值保留。删除时取上游 `applicant`。旧记录完全未知时为空。 |
| `applicant_note` | 申请备注，默认空字符串，最多 10000 字符。 |
| `reviewer` | 本次注册中心审核员，未审核时为空。 |
| `reviewer_note` | 审核意见，支持 Markdown，未提供时为空，最多 10000 字符。 |
| `status` | 申请状态：`pending`、`approved`、`rejected`，默认 `pending`。 |
| `requested_at`、`reviewed_at` | 申请与审核时间，UTC ISO 8601 文本；审核时间未知时为空。 |
| `created_at`、`updated_at` | UTC 创建与更新时间；更新语句显式维护更新时间。 |

MySQL 正式资源不保存消息 ID、offset 或来源审核员。审核通过时从本表复制申请人、申请备注、注册中心审核员、审核备注和申请时间；备注同时作为正式资源的入库审核快照保存。推荐列表仍仅保存在本表。

申请状态仅属于 SQLite。MySQL 不再有 status 字段，也不通过 ready、deleted 枚举表示资源存在性。

---

## 入库审核表

`ingestion_reviews` 共 25 个字段：公共申请与审核字段，以及下列入库字段。待审核的新文件没有 MySQL 记录，因此这里保留完整文件快照；其中 `file_url` 指向将来正式资源使用的原文件。

| 字段 | 含义 |
| --- | --- |
| `source_reviewer` | 保留上游消息 `reviewer` 原值的兼容列。当前契约中该人就是入库申请人，不是另一个角色；待审列表和审核历史不单独返回此字段。 |
| `file_url` | 平台目录内的原文件名，不带斜线，最多 146 字符。 |
| `name` | 文件名称，最多 200 字符，历史缺失时为空。 |
| `abstract` | 文件摘要，最多 10000 字符，历史缺失时为空。 |
| `embedding` | 摘要向量，JSON 数组文本，尚未生成时为空。 |
| `passport_recommendations` | `{passport, reason}` 推荐列表，JSON 数组文本；NULL 表示未推荐，`[]` 表示已推荐但无候选。 |
| `file_level` | INTEGER，可空且必须是整数。新批准时必填，决定预留后冻结；待审、拒绝及未知历史为 NULL。 |
| `passport` | 人工最终通行证数组，JSON 文本；每项最多 512 字符，新批准时非空，拒绝为 `[]`，尚未决定和历史未知为 NULL。 |

人工决定预留后，`reviewer`、`reviewer_note`、`reviewed_at` 及批准时的 `file_level` 已确定；存储处理完成前 `status` 仍为 `pending`。待审列表只查询 `status='pending' AND decision IS NULL`。存储完成后 `status=decision`，通过关联正式资源 ID；反馈发布完成后保存 `result_message_id`。通行证建议与最终通行证分别保存，不互相覆盖。

当前中间件[入库消息](../../../../../知识沉淀中间件服务/middleware-service/description/architecture/data/ingestion-request.md)没有独立 `applicant` 字段，来源提交人位于 `reviewer`。入库 DTO 兼容历史显式 `applicant`，缺失时映射上游 `reviewer`；新待审和自动拒绝记录据此保存申请人。注册中心最终审核人仍由人员登录或自动审核身份独立确定。

旧实现可能只写入 `source_reviewer` 而留下空 `applicant`。列表和历史使用 `COALESCE(applicant,source_reviewer)` 返回申请人，不改写记录；原消息重投仍按来源身份核对，待审重投或首次人工决定时补齐申请人，使批准后 MySQL 正式资源得到正确申请人。非空历史申请人不覆盖，已确定结论不重置；保留旧列兼容历史，不执行删列或批量迁移。

待审核原文件保存到 `{REVIEW_LOG_ROOT}/tmp-file/ingestion/{source_platform}/{file_url}`，不把 PDF 字节或页面图片塞进 SQLite。工作流持久化前验证向量与推荐条目，并保存完整 JSON 结果；后台接收服务已运行完整工作流，新待审申请保存向量与完整推荐列表。历史记录可能保留 NULL，本次切换不自动回填。

`(source_platform, source_id)` 对 `pending` 入库记录建立部分唯一索引，防止同平台同源并发产生多条待审核申请；缺失源 ID 不占用名额。不同消息命中既有 pending 申请时走自动拒绝，原申请不被覆盖。已入库和待删除文件的拦截通过只读查询正式资源完成。

---

## 删除审核表

`deletion_reviews` 共 23 个字段：公共申请与审核字段，以及下列删除字段。文件名称、摘要和原上传人保存为申请时的独立快照，资源删除后仍能展示；不复制向量或推荐列表。正式文件身份通过 `resource_id` 关联，来源身份保留用于请求校验和反馈。

| 字段 | 含义 |
| --- | --- |
| `name` | 删除消息中的业务文件名称快照，最多 200 字符；不是磁盘文件名，历史未知时为 NULL。 |
| `abstract` | 删除消息中的文件摘要快照，最多 10000 字符，历史未知时为 NULL。 |
| `file_level` | 从 MySQL 正式资源取得的文件等级快照，INTEGER 可空；历史未知或自动拒绝未绑定资源时为 NULL，0 为有效等级。 |
| `uploader` | 删除消息中的原文件上传人，最多 200 字符，与删除申请人分开，历史未知时为 NULL。 |
| `passport` | 子平台提交的已有文件通行证数组，JSON 文本，每项最多 512 字符；用于核对完整集合，不生成或推荐新的通行证。 |
| `file_url` | 正式资源的原文件名，不带斜线，最多 146 字符；从 MySQL 资源记录取得，读取根目录为 RESOURCE_ROOT（默认 data/resource）。自动拒绝且未绑定资源时为空。 |

中间件的[删除申请消息契约](../../../../../知识沉淀中间件服务/middleware-service/description/architecture/data/deletion-request.md)没有来源审核员，因此删除表不包含 `source_reviewer`。`applicant` 表示本次删除申请人，`applicant_note` 表示删除原因，`reviewer` 与 `reviewer_note` 表示注册中心的最终审核人员和意见。

新删除申请不再上传文件。消费者按平台码与 source_id 定位正式资源，核对 passport 并检查原文件存在，保存 resource_id、正式 file_url 和 file_level 快照后 ACK。原文件读取位置为 `{RESOURCE_ROOT}/{source_platform}/{file_url}`，待审核期间不复制、移动或删除。消息中的 name、abstract、uploader 在待审及自动拒绝分支首次写入本表，不用于磁盘路径或资源身份校验；重投不覆盖已保存快照。待审列表与审核历史直接从本表返回快照，不依赖 MySQL 资源是否存在。

资源不存在或通行证不符时，保存无资源绑定的 rejected 记录；同一资源已被另一条删除审核绑定时也拒绝新申请，保留旧记录。自动拒绝的 resource_id、file_url 为 NULL，保存申请、审核意见及反馈凭据后 ACK。详情见[删除申请接收服务](../../capability/application/deletion-consumer.md)。

删除表的唯一索引保持原契约。SQLite schema 版本为 10；版本 10 在删除表增加可空 `file_level`，仅从资源唯一 ID、平台、源 ID 全部一致的已批准入库记录恢复等级，已有非空快照不覆盖，无法恢复时保留 NULL；版本 8 引入删除文件快照，版本 9 在入库表增加可空 `file_level`，旧等级保持 NULL，不推测或自动改写原反馈。旧记录的名称、摘要仅从资源唯一 ID、平台、源 ID 全部一致的已批准入库审核记录补齐，已有非空值不覆盖；未关联或无法可靠恢复时为 NULL。旧上传人保持 NULL，不用入库申请人代替。升级不访问 MySQL，不修改原决定、反馈凭据或审核时间；通行证转换规则见[通行证数组契约](passport.md)。历史 file_url 若曾保存删除附件，不能自动解释成正式文件；历史身份或正式引用不一致时停止消费等待核对，不猜测路径。

---

## 写入与恢复

正常接收先保存 ingestion 暂存目录中的原文件，再提交 SQLite 待审核申请，返回 `review_id` 后 ACK；不创建 MySQL pending 记录。文件成功、SQLite 失败时保留完整附件，重投复用文件并补齐申请；SQLite 中已保存的申请可以恢复 ACK，缺失原附件时只补回暂存文件，不重新运行智能体。

拒绝分支保存 `rejected`、Markdown 意见及“注册中心自动审核员”，随后发布反馈、保存 `result_message_id`，最后 ACK。已保存反馈消息 ID 时重投只恢复 ACK；发布成功但凭据尚未保存的窗口仍不能将中间件 409 当作成功。

重投核对申请身份、人员、备注、申请时间和已有文件快照。已有审核结果不重置，向量和推荐结果只补齐 pending 的空字段。

人工审核已通过[入库审核 API](../../api/ingestion-review.md)接入：从本表加载完整申请，预留决定，通过后在原件晋升完成时提交 MySQL 正式资源，并回填 `resource_id`；拒绝只更新本表和反馈。恢复器仅扫描人工 `decision` 非空且反馈凭据缺失的记录；具体阶段与冲突规则见[人工入库审核服务](../../capability/application/ingestion-review.md)。后台消费遇到人工决定只恢复 ACK，不重新运行智能体或自动发布。

删除人工审核已通过[删除审核 API](../../api/deletion-review.md)接入：批准先执行正式资源删除和原件保留，再完成 SQLite 结论并发布反馈；拒绝只完成 SQLite 和反馈。决定预留后人员、意见及时间确定，执行完成前 status 仍为 pending，待审列表排除已预留决定；历史仅展示 approved/rejected，本地完成但发布凭据缺失的记录仍可查询。原 resource_id 和 passport 不清空，批准删除不清除审核历史。阶段恢复与重投 ACK 见[人工删除审核服务](../../capability/application/deletion-review.md)。

---

## 文件目录与迁移

审核数据库默认位于 `data/review-log/registry.db`。入库原文件在待审核期间存于 `data/review-log/tmp-file/ingestion/`，正式入库后转入 `data/resource/`；最终删除后的原文件转入 `data/review-log/tmp-file/deletion/` 保留，暂不自动清理。文件转移接口与旧目录迁移统一见[审核日志与文件存储](../../capability/infrastructure/review-file-storage.md)。

---

## 人工审核身份

新人工决定中的注册中心审核人使用登录身份的 `userid`；历史姓名和固定自动审核员名称保持原值，不自动转换。字段仍为字符串，不增加员工外键，以保留员工移除后的审计记录。认证与兼容边界见[登录身份契约](authenticated-user.md)。
