# 待审核文件持久化

> **用途：** 文件确认不重复、通行证推荐成功后，将原文件与完整申请保存到 SQLite，再由主图 ACK。待审核阶段不写入 MySQL。

---

## 接入方式

`PendingReviewService` 接收只读正式资源仓库、文件存储、SQLite 审核仓库和文件大小上限。共享连接由创建者管理；服务不 ACK、不发布审核结果。

```python
from app.infrastructure.resource_store import ResourceFileStore
from app.service.pending_review import PendingReviewService

context.pending_review_service = PendingReviewService(
    repository,
    ResourceFileStore(ingestion_settings.ingestion_file_root),
    max_file_bytes=ingestion_settings.max_file_bytes,
    reviews=review_repository,
)
```

主图仅在 `is_duplicate=False` 且推荐结果已完成时调用持久化；NULL 不等于成功的空数组。返回 SQLite 主键后设置 `pending_review_saved=True` 和 `review_id`，不返回尚未创建的 MySQL 资源 ID。

---

## 保存内容与顺序

1. 验证原文件非空和大小上限，验证摘要向量与推荐条目。
2. 从 SQLite 检查其他同平台同源的 pending 申请，并只读检查 MySQL 已入库资源；冲突时不写入原文件。
3. 将原文件保存到 `{REVIEW_LOG_ROOT}/tmp-file/ingestion/{platform_code}/{file_id}.{extension}`。
4. 在 SQLite 事务中保存申请、文件元信息、摘要向量和完整 `{passport, reason}` 推荐列表，状态为 `pending`，`resource_id` 为空。
5. 返回 `review_id`，主图再次确认 SQLite 记录存在后 ACK。

正式资源目录不会因待审核落盘写入新文件；审批通过后的文件晋升能力见[审核文件存储](../infrastructure/review-file-storage.md)。

字段定义与正式入库边界见[审核表契约](../../architecture/data/review.md)。向量复用前置结果，明确无推荐保存 JSON `[]`；本服务不调用模型，也不创建正式 MySQL 文件记录。

---

## 文件发布与重试

原文件先写临时文件并刷盘，再通过硬链接原子发布，禁止覆盖已有附件。并发处理同名文件时读取并比对原字节，内容不一致停止，不 ACK。

SQLite 保存失败时保留完整文件，重试验证并复用，不重复改写文件。同一消息复用 SQLite 主键，不重置人工结论；只补齐 pending 的空向量与推荐结果。同平台同源的并发新消息由 SQLite 部分唯一索引兜底，失败方改走拒绝分支。

文件系统与 SQLite 没有统一事务，故障可能留下尚无申请记录的完整附件。当前没有孤立附件自动回收任务。已保存申请的重投由主图直接恢复 ACK；原附件丢失时只补回文件，不重复向量化、判重或推荐。

---

## 验证与当前边界

```bash
python -m unittest discover -s tests -p 'test_pending_review.py'
```

测试覆盖完整 SQLite 快照先于 ACK、SQLite 失败重试、文件冲突、无效输入、SQLite 同源拦截、人工结论保护及完整工作流。隔离 MySQL 集成验证使用仅 SELECT 权限的账号，确认正常待审核落盘不写入 MySQL。

判重与通行证推荐智能体已实现，主图已接入[后台接收服务](ingestion-consumer.md)，新申请通过完整流程生成向量与推荐结果后暂存。[人工入库审核服务](ingestion-review.md)已实现审批与正式资源入库；删除消费见[删除申请接收服务](deletion-consumer.md)。
