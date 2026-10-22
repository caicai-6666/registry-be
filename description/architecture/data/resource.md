# 注册中心资源表契约

`resources` 只保存已经通过入库审核的正式文件。待审核申请、审核状态和推荐过程保存在 [SQLite 审核表](review.md)，通过唯一资源 ID 关联。建表执行来源为 [001-resources.sql](../../../../registry-deploy/mysql/init/001-resources.sql)，由 MySQL 容器首次初始化时创建；后端启动不执行 MySQL DDL。

---

## 字段与来源

| 字段 | 类型 | 含义与来源 |
| --- | --- | --- |
| `id` | `BIGINT UNSIGNED` | 注册中心正式资源自增主键。 |
| `source_platform` | `VARCHAR(128)` | 文件来源平台代号，用于定位文件目录。 |
| `source_id` | `VARCHAR(128)`，可空 | 文件在子平台中的 ID；历史缺失时为空。 |
| `file_url` | `VARCHAR(146)` | 平台目录内的原文件名，不带斜线。 |
| `name` | `VARCHAR(200)`，可空 | 文件业务名称，不用于构造磁盘路径。 |
| `abstract` | `TEXT`，可空 | 文件摘要。 |
| `applicant` | `VARCHAR(200)`，可空 | 已通过入库申请的申请人，历史未知时为空。 |
| `applicant_note` | `TEXT` | 该次入库申请人的备注，允许空字符串。 |
| `reviewer` | `VARCHAR(200)`，可空 | 批准入库的注册中心审核员，历史未知时为空。 |
| `reviewer_note` | `TEXT`，可空 | 批准入库时的审核员备注，历史未知时为空。 |
| `requested_at` | `DATETIME(6)` | 已通过入库申请的申请时间，UTC。 |
| `embedding` | `JSON`，可空 | 文件摘要向量，未生成的历史资源为空。 |
| `file_level` | `BIGINT`，可空 | 文件当前等级，初次由审核员批准时确定、后续可由管理员调整；有符号 64 位整数；历史未知为 NULL，不预设业务枚举。 |
| `passport` | `JSON`，可空 | 最终通行证字符串数组，一份文件可有多个；历史未知时为空。 |
| `created_at` | `DATETIME(6)` | 正式资源记录创建时间，UTC。 |
| `updated_at` | `DATETIME(6)` | 正式资源记录更新时间，UTC。 |

[正式文件基本信息接口](../../api/catalog.md)将 `created_at` 以 `ingested_at` 返回，表示正式记录创建时间；不增加表字段。`requested_at` 保留为原入库申请时间。

共 16 个字段。`status`、`passport_recommendations` 不再属于资源表；`message_id`、`request_offset`、`source_reviewer` 仍仅在 SQLite 审核记录中保存。

正式入库应从 SQLite 已通过的入库申请复制文件数据、文件等级、申请人、申请备注、注册中心审核员及审核备注；来源平台审核员不能替代注册中心审核员。两类备注是已通过审核的快照，不承载之后的删除审核意见。

---

## 文件与存储边界

已正式入库的原文件位置由资源根目录、来源平台和文件名组成：

```text
{RESOURCE_ROOT}/{source_platform}/{file_url}
```

`RESOURCE_ROOT` 默认 `data/resource`；`file_url` 只保存 `<file_id>.<小写后缀>`，不是 HTTP URL、绝对路径或带斜线的地址。标识和后缀必须经过校验，不使用业务名称构造路径。读取与渲染见[资源 PDF 服务](../../capability/document/resource-pdf.md)。

SQLite pending 入库申请不创建 MySQL 资源，原文件暂存在 `data/review-log/tmp-file/ingestion/`；拒绝也不创建资源。MySQL 行存在即表示文件已入库，不再维护待入库、待删除或 deleted 状态。删除待审核时仅记录 SQLite 删除申请；最终批准并执行删除后，正式资源行应移除，审核历史继续保留在 SQLite，原文件转入 `data/review-log/tmp-file/deletion/`，暂不自动清理。

人工入库审核已通过[审核 API](../../api/ingestion-review.md)实现：批准时复制 SQLite 完整快照并迁移原件后提交正式资源；拒绝不创建资源。[人工删除审核](../../capability/application/deletion-review.md)已实现：批准时按原资源 ID 锁定并校验身份，原件保留成功后提交 DELETE，SQLite 完成本地结论后向中间件反馈；拒绝保留正式资源及原件。

---

## 向量与通行证

`embedding` 非空时必须是非空 JSON 数组；应用还需验证向量数值、模型和维度。`MySQLResourceRepository.list_vectors` 查询有向量的正式资源，传入平台时限定来源平台，不传时读取全平台，并携带通行证；不附加审核状态条件。正式检索会按保存的摘要和当前固定指令重新生成候选向量，再同步到可重建 HNSW 缓存；旧 embedding 缺少编码来源信息，不直接与新查询向量比较。重编码使用有界进程内缓存，不改写本表或历史审核快照；具体规则见[固定摘要指令及兼容处理](../../capability/infrastructure/myvector.md#固定摘要相似检索指令)。

`passport` 保存最终确认的通行证数组，允许不同资源共享其中的值，不能作为唯一文件身份。元素校验、删除全集匹配与兼容规则见[通行证数组契约](passport.md)。候选列表与推荐理由只保存在 SQLite 的 `ingestion_reviews.passport_recommendations`，不复制进 MySQL。

---

## 身份与索引

- `id` 是资源主键；每张 SQLite 审核表的非空 `resource_id` 唯一关联它。
- `(source_platform, file_url)` 唯一，避免正式文件重复建档。
- `(source_platform, source_id)` 唯一，同平台同源只能存在一份正式资源；NULL 源 ID 不占用名额。
- `passport` 使用 JSON 数组，不再保留旧字符串普通索引；同一文件可持有多个通行证。

MySQL 不再使用基于状态的函数唯一索引或平台状态索引。SQLite pending 同源唯一约束负责待审核申请，MySQL 正式源身份唯一约束负责已入库文件。正式记录移除后可以重新入库，详细流程见[源文件身份拦截](../../capability/application/source-identity.md)。

---

## 初始化与版本边界

当前 MySQL 仅维护最终版本的[初始化定义](../../../../registry-deploy/mysql/init/001-resources.sql)，由容器在空数据目录首次启动时执行。历史增量迁移文件及后端 MySQL 升级工具已移除，新部署无需依次执行旧版本变更。

已有数据卷不会重新执行初始化脚本，`CREATE TABLE IF NOT EXISTS` 不能升级已有表。旧结构如需继续使用，应先备份并单独制定数据转换方案；当前后端按本文的正式资源契约运行。

此边界仅针对 MySQL。SQLite 审核数据库仍由后端启动时独立初始化和自动升级，规则见[SQLite 基础设施](../../capability/infrastructure/sqlite.md)。

---

## 人工审核身份

新人工决定中的注册中心审核人使用登录身份的 `userid`；历史姓名和固定自动审核员名称保持原值，不自动转换。字段仍为字符串，不增加员工外键，以保留员工移除后的审计记录。认证与兼容边界见[登录身份契约](authenticated-user.md)。

---

## 文件等级与升级

新批准必须显式提供 `file_level`，与 SQLite 已冻结决定保持一致，重试不允许变更等级。正式资源记录保存该值，目录接口返回同名字段。文件等级独立于平台管理权限，不定义业务枚举。正式原件读取要求用户在 `source_platform` 的文件访问等级大于或等于 `file_level`，或为注册中心管理员；历史 null 等级仅允许管理员读取。执行规则见[授权依赖](../../capability/application/authorization.md)。

已有资源的等级保持 NULL，不回填为 0。初始化脚本只对新表生效；已有库需按 [MySQL 升级说明](../../../../registry-deploy/mysql/readme.md#文件等级字段)增加字段，不通过重建或清空业务卷升级。


正式入库后的等级通过 [catalog 等级调整接口](../../api/catalog.md#调整正式文件等级)修改；当前值保存在本表，历史过程保存在 [SQLite 文件等级调整日志](file-level-adjustment.md)，原入库审核等级不改写。
