# 审核日志与文件存储

本能力将审核数据库、待审核原文件、正式资源和删除保留文件分开存放。审核数据契约见[审核表](../../architecture/data/review.md)，正式资源契约见[资源表](../../architecture/data/resource.md)。

---

## 目录与配置

```text
data/
├── resource/
│   └── <platform_code>/<file_name>
└── review-log/
    ├── registry.db
    └── tmp-file/
        ├── ingestion/
        │   └── <platform_code>/<file_name>
        └── deletion/
            └── <platform_code>/<file_name>
```

```dotenv
RESOURCE_ROOT=data/resource
REVIEW_LOG_ROOT=data/review-log
SQLITE_PATH=data/review-log/registry.db
```

`RESOURCE_ROOT` 只存正式资源；`REVIEW_LOG_ROOT` 保存审核日志与暂存文件。相对目录以项目根目录解析，正式资源根目录和审核日志根目录不能重叠。未显式配置 `REVIEW_LOG_ROOT` 时取 `RESOURCE_ROOT` 的同级 `review-log`，便于测试与独立部署一起隔离目录。

未配置 `SQLITE_PATH` 时使用审核日志根目录下的 `registry.db`；显式配置时以 `SQLITE_PATH` 为准。容器继续持久化 `/workspace/data`，其中同时包含正式资源、SQLite 主文件及 WAL 文件、暂存与保留文件。测试文件均在 `tests/tmp/` 中创建并清理。

应用启动准备三类文件目录，挂载到 `app.state.review_file_service`。目录存在不代表有申请，也不会因初始化清理删除保留文件。

---

## 入库暂存与正式文件

后台消费者执行完整 LangGraph，待审核落盘节点使用 `ingestion` 目录。原文件落盘后提交 SQLite pending 申请，再 ACK；原文件不会提前进入 `resource`。SQLite 只保存平台目录内的文件名，平台与文件名不随文件移动而改变。

入库审核通过时，[人工入库审核服务](../application/ingestion-review.md)调用 `ReviewFileService.promote_ingestion(platform_code, file_name)` 将文件转到正式资源目录，再提交 MySQL 正式入库并更新 SQLite 关联。该方法只转移文件，不代替审批、数据库提交或中间件反馈。

尚无人工决定的重投读取由审核状态决定：pending 使用 ingestion 目录，approved 使用 resource 目录。人工 `decision` 已确定的记录仅恢复消费 ACK，由审核服务恢复后续处理，避免重新补文件或自动发布。缺失待审原文件时只补回暂存文件；关联的正式资源已经移除时，旧消息重投不恢复原文件。

相似文件和正式资源 PDF 的默认读取仍使用 `RESOURCE_ROOT`；当前申请在判重前直接从内存渲染，不依赖正式目录已有文件。需要读取待审核 PDF 的调用方可显式向 `ResourcePDFService` 传入 ingestion 根目录，不自动跨目录回退查找。

---

## 删除后保留

[人工删除审核服务](../application/deletion-review.md)调用 `ReviewFileService.retain_deleted(platform_code, file_name)`，把原正式文件转到 deletion 保留目录。删除申请不上传附件，文件名取自申请关联的正式资源；待审期间通过 resource 引用原件。

删除审核通过后，正式资源表移除对应行，文件在 deletion 目录继续保留，历史申请与审核结论仍存于 SQLite。业务执行在 MySQL 事务内完成原件保留后提交 DELETE，再更新 SQLite 并发布反馈；恢复时使用 `read_deleted` 校验保留原件。

> **清理策略：** 暂不启用自动清理，不设保留期、不按文件时间直接删除。删除保留文件会持续保存，后续明确保留期后再接入清理任务。

---

## 文件转移与失败边界

`ResourceFileStore.move_to` 校验平台与文件名，先将源文件流式复制到目标目录的临时文件并刷盘，再独占发布目标，最后删除源文件并同步目录。此方式支持不同文件系统，不覆盖已有目标。

- 源存在、目标不存在：完整发布目标后移除源。
- 源与目标都存在且字节相同：复用目标，移除残留源文件，恢复中断的移动。
- 源与目标内容不同：失败，两份文件都保留。
- 源已不存在、目标存在：按已完成移动处理，保留目标原时间与内容。
- 两边都不存在、路径非法、符号链接或非普通文件：失败，不当作成功。

文件移动与两类数据库不构成统一事务。两类人工审核已使用 SQLite 决定记录、MySQL 事务和幂等文件移动恢复各阶段；只调用文件方法不能据此认定正式入库或删除已经成功。

---

## 旧目录迁移

停止后端，确认旧 SQLite 没有活动写入，在项目根目录执行：

```bash
python scripts/migrate_review_storage.py --apply
```

默认旧库为 `data/registry.db`，可以通过 `--source-database` 指定；目标来自当前 SQLite 配置。脚本使用 SQLite 在线备份包含已提交 WAL 数据，检查完整性并比较完整数据库内容，再只将 SQLite pending 入库申请对应的文件从 resource 移到 ingestion。已通过审核的正式文件不移动。

所有待审文件迁移完成、两份数据库一致后才移除旧库及旧 WAL 附属文件。目标已有不同数据库时中止，不覆盖、不自动合并。缺文件或缺失历史文件名时保留旧库，不猜测原文件名。迁移可重跑，删除保留目录不会被清理。

临时备份在发布前完成 WAL 检查点，成功或失败后都清理本次 `.registry-*` 主文件及附属文件。正式 `registry.db-wal`、`registry.db-shm` 是 SQLite 正常运行文件，由引擎管理，不属于迁移临时产物。

---

## 验证

```bash
python -m unittest tests.test_review_files tests.test_pending_review tests.test_ingestion_consumer
```

验证覆盖目录隔离、待审只落盘暂存目录、正式文件晋升、删除保留、重试、目标冲突和路径校验，以及旧库迁移和 WAL 完整备份。
