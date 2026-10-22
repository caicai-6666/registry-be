# 文件等级调整日志

`file_level_adjustments` 是 SQLite 独立审计表，保存正式文件等级变更及中间件发布凭据。数据库位置复用 `SQLITE_PATH`，默认 `data/review-log/registry.db`；不创建新 MySQL 表。业务流程与接口见[文件等级调整](../../capability/application/file-level-adjustment.md)和 [catalog API](../../api/catalog.md#调整正式文件等级)。

---

## 字段

| 字段 | 类型与含义 |
| --- | --- |
| `id` | INTEGER，自增日志 ID。 |
| `resource_id` | TEXT，注册中心正式文件 ID 的十进制字符串，兼容 MySQL 无符号 BIGINT。 |
| `source_platform`、`source_id`、`file_url` | 正式文件身份快照；用于重试校验和发布，日志在文件删除后仍保留。 |
| `previous_file_level` | INTEGER，可空；调整前等级，历史未知为 NULL。 |
| `file_level` | INTEGER，调整后的等级，有符号 64 位整数；0 和负数均按现有等级契约接收。 |
| `reviewer` | 操作人的可信 userid。 |
| `reviewer_name` | 创建日志时的操作人名称；缺失时使用 userid，中间件 reviewer 使用此快照。 |
| `note` | 调整备注，最多 10000 字符，允许为空。 |
| `status` | `prepared`、`local_updated` 或 `published`。 |
| `result_message_id` | 已确认发布的中间件消息 ID；未确认时为 NULL。 |
| `last_error` | 最近一次执行异常的类型及 HTTP 状态码，不保存上游正文、SQL 或凭据。 |
| `created_at`、`updated_at` | 带时区的 UTC 时间；创建时间不因重试变化。 |

---

## 写入及恢复

表和部分唯一索引由 [SQLiteFileLevelAdjustmentRepository](../../../app/repository/file_level_adjustment.py) 使用 `CREATE ... IF NOT EXISTS` 初始化，应用启动时完成；不改变两张审核表的 schema 版本。`resource_id` 在未完成状态上唯一，已经发布的记录不删除，允许同一文件后续产生多次调整日志。

先提交 `prepared` 日志，再通过 MySQL 行锁确认资源身份及原等级并修改等级；MySQL 提交后标记 `local_updated`；中间件返回合法 201 和消息凭据后标记 `published`。日志保留调整意图，因此步骤失败不会丢失待核对的操作。

MySQL 提交与 SQLite 标记之间失败时，`prepared` 不表示 MySQL 一定未更新。重试允许当前等级等于原等级或目标等级，其他等级或资源身份变化时停止处理。`local_updated` 表示本地提交已确认，不能据网络失败回滚到旧等级。

入库审核等级保留最初批准值；删除审核等级保留申请时快照，两者不随正式资源后续等级调整改写。正式资源当前值以 MySQL `resources.file_level` 为准，完整变更过程以本表为准。


## 历史读取

[文件等级调整历史接口](../../api/catalog.md#获取文件等级调整历史)向全部 registry 管理员返回所有操作人的记录，包含三个处理状态，按创建时间和日志 ID 倒序排列。直接读取本表快照，不关联 MySQL；文件删除后日志继续展示。返回操作人的 userid 与姓名快照，不返回 `file_url` 或 `last_error`。查询不改变日志或触发发布。
