# 入库源文件身份拦截

> **规则：** 相同来源平台与 `source_id` 已有 SQLite pending 入库申请，或者已有 MySQL 正式资源时，拒绝新的入库申请。

---

## 入口与判定

主图与后台消费者在下载前先查询 SQLite，再只读查询 MySQL。

| 已有数据 | 新申请处理 |
| --- | --- |
| SQLite pending 入库申请 | 拒绝，说明文件仍待入库审核。 |
| MySQL 正式资源 | 拒绝，说明文件已入库。 |
| SQLite pending 删除申请，正式资源仍存在 | 正式资源仍阻断新入库申请。 |
| 正式资源已移除，也没有 pending 入库申请 | 允许重新申请入库。 |

精确身份检查不依赖摘要向量、相似度或智能体。不同平台的相同源 ID 不互相阻断；历史缺失 `source_id` 时不把 NULL 当作共同身份。

MySQL 不再查询资源状态，行存在即表示正式入库。入库与删除审核状态仍在 SQLite 中维护。

---

## 拒绝与重投

命中时，用已有文件 `source_id` 和待审核或已入库的明确理由构造 [Markdown 审核意见](../../architecture/workflow/ingestion/readme.md#拒绝结果契约)，审核员固定为“注册中心自动审核员”，`passport=[]`。保存 SQLite 拒绝记录、成功发布反馈并保存反馈消息 ID 后，才 ACK。

同一消息属于消费重投，从 SQLite 核对身份与文件快照，已保存申请直接恢复 ACK；缺少附件时只补回原文件，不重新调用智能体。已通过审核但关联的正式资源已不存在时，不因旧消息重投恢复文件。

SQLite 已有反馈消息 ID 时可跨进程恢复拒绝 ACK，不重复发布；发布已成功但凭据尚未保存时，仍不能把中间件 409 直接当作成功。

`interrupt_before_comparison=True` 同时暂停拒绝发布、原文件恢复和 ACK，前置验证不产生这些副作用。

---

## 并发约束

SQLite 部分唯一索引约束 `(source_platform, source_id)` 在 pending 入库记录中唯一；MySQL 普通唯一索引 `uq_resources_platform_source` 约束正式资源身份，不再使用状态表达式。

持久化在写文件前复查 SQLite 和正式资源。SQLite 并发冲突返回 `ActiveSourceConflict`，主图改走拒绝分支，不能覆盖先前申请。晚到冲突可能留下完整但未关联的附件，回收边界见[持久化说明](pending-review.md)。

正式资源物理移除后释放源身份；删除审核尚未完成时资源仍存在，不提前释放。

---

## 部署与验证

新库初始化包含正式源身份唯一约束；当前不再提供旧版 MySQL 索引迁移脚本，版本边界见[资源表说明](../../architecture/data/resource.md#初始化与版本边界)。

```bash
python -m unittest discover -s tests -p 'test_source_identity.py'
```

测试覆盖 SQLite pending 拦截、无状态正式资源拦截、跨平台隔离、资源移除后放行、重投及原文件恢复、发布失败不 ACK、前置模式和后台拒绝重试。
