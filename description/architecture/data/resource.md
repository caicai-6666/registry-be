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
| `passport` | `JSON`，可空 | 最终通行证字符串数组，一份文件可有多个；历史未知时为空。 |
| `created_at` | `DATETIME(6)` | 正式资源记录创建时间，UTC。 |
| `updated_at` | `DATETIME(6)` | 正式资源记录更新时间，UTC。 |

[正式文件基本信息接口](../../api/catalog.md)将 `created_at` 以 `ingested_at` 返回，表示正式记录创建时间；不增加表字段。`requested_at` 保留为原入库申请时间。

共 15 个字段。`status`、`passport_recommendations` 不再属于资源表；`message_id`、`request_offset`、`source_reviewer` 仍仅在 SQLite 审核记录中保存。

正式入库应从 SQLite 已通过的入库申请复制文件数据、申请人、申请备注、注册中心审核员及审核备注；来源平台审核员不能替代注册中心审核员。两类备注是已通过审核的快照，不承载之后的删除审核意见。

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

`embedding` 非空时必须是非空 JSON 数组；应用还需验证向量数值、模型和维度。`MySQLResourceRepository.list_vectors` 查询有向量的正式资源，传入平台时限定来源平台，不传时读取全平台，并携带通行证；不附加审核状态条件。候选检索将其同步到可重建的 HNSW 缓存。

`passport` 保存最终确认的通行证数组，允许不同资源共享其中的值，不能作为唯一文件身份。元素校验、删除全集匹配与兼容规则见[通行证数组契约](passport.md)。候选列表与推荐理由只保存在 SQLite 的 `ingestion_reviews.passport_recommendations`，不复制进 MySQL。

---

## 身份与索引

- `id` 是资源主键；每张 SQLite 审核表的非空 `resource_id` 唯一关联它。
- `(source_platform, file_url)` 唯一，避免正式文件重复建档。
- `(source_platform, source_id)` 唯一，同平台同源只能存在一份正式资源；NULL 源 ID 不占用名额。
- `passport` 使用 JSON 数组，不再保留旧字符串普通索引；同一文件可持有多个通行证。

MySQL 不再使用基于状态的函数唯一索引或平台状态索引。SQLite pending 同源唯一约束负责待审核申请，MySQL 正式源身份唯一约束负责已入库文件。正式记录移除后可以重新入库，详细流程见[源文件身份拦截](../../capability/application/source-identity.md)。

---

## 旧库升级

新库直接使用当前初始化脚本。已有库停止后端并备份 MySQL 和 SQLite，在 `registry-be` 根目录执行：

```bash
python scripts/migrate_resource_reviews.py --apply
python scripts/migrate_formal_resources.py --apply
python scripts/migrate_passport_arrays.py --apply
```

第一步通过 [008-resource-review-storage.sql](../../../../registry-deploy/mysql/migrations/008-resource-review-storage.sql) 将旧消息与审核信息迁入 SQLite，旧 MySQL 待入库记录完整转存后移出正式表。已经采用最新无状态表时，该旧迁移脚本直接跳过，不会删除重新加入的入库备注。

第二步锁定资源表，按资源 ID 查询 SQLite 中已通过的入库审核，校验来源身份，回填申请人、审核员与两类备注，原推荐列表归档到 SQLite。逐字段验证后调用 [009-formal-resources.sql](../../../../registry-deploy/mysql/migrations/009-formal-resources.sql)，移除状态、推荐字段及相关约束，建立正式源身份唯一索引。

第三步通过[010 数组切换](../../../../registry-deploy/mysql/migrations/010-resource-passport-arrays.sql)将旧单通行证升级为 JSON 数组，保留资源 ID 与更新时间，迁移及重试规则见[通行证数组契约](passport.md#已有数据库升级)。

缺失审核记录、审核身份或备注冲突时中止，不猜测内容、不覆盖审核结论。旧库仍有待入库或 deleted 行时，须先处理历史迁移与删除，不能直接丢弃状态把它们当作正式资源。两条命令可重跑；MySQL DDL 不提供整个跨库过程的事务回滚，不直接对含数据旧库执行删列 SQL。

更早版本的文件名、字段简化与索引迁移继续由部署项目 `mysql/migrations/001` 至 `007` 的历史脚本维护；不要在最新结构上重跑这些旧脚本。
