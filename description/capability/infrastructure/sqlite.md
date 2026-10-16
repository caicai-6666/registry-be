# SQLite 审核数据基础设施

> **当前阶段：** 已提供异步连接、事务、配置、应用生命周期和两张审核表。入库审核仓库已接入接收服务和工作流；删除审核仓库已接入[删除申请接收服务](../application/deletion-consumer.md)与[人工删除审核](../application/deletion-review.md)。

本文说明 SQLite 的配置与运行方式。审核表字段、状态和关联约束统一见[注册中心审核表契约](../../architecture/data/review.md)。

---

## 存储与配置

```dotenv
REVIEW_LOG_ROOT=data/review-log
SQLITE_PATH=data/review-log/registry.db
SQLITE_BUSY_TIMEOUT_SECONDS=5
```

默认数据库位于项目根目录的 `data/review-log/registry.db`，容器中对应 `/workspace/data/review-log/registry.db`，使用现有 `/workspace/data` 持久化挂载。相对路径以项目根目录解析，不受进程当前目录影响；进程环境覆盖 `.env`。锁等待上限为正数秒，默认 5 秒。

数据库初始化按需创建父目录，不支持内存数据库或 SQLite URI 路径。数据库和可能产生的 `-wal`、`-shm` 文件属于真实运行数据，默认均留在 `data/review-log/`；测试显式配置 `tests/tmp/`，遵循[测试与实验规范](../../standard/experiment.md)。

---

## 连接与事务

`SQLiteDatabase` 使用现有 `aiosqlite` 依赖，由后台工作线程执行 SQLite 操作。每个实例保留一个连接，通过异步锁保护连接上下文和完整事务，不把两个协程的写入混到同一个事务。

初始化启用 WAL、`synchronous=FULL`、外键检查和锁等待时间。WAL 协议及同步含义见 [SQLite 官方说明](https://www.sqlite.org/wal.html)，驱动接口见 [aiosqlite 文档](https://aiosqlite.omnilib.dev/en/stable/api.html)。事务之间在本实例内串行执行；该基础设施不提供多机器共享 SQLite 文件能力。

```python
from app.config.sqlite import load_sqlite_settings
from app.infrastructure.sqlite import SQLiteDatabase

async with SQLiteDatabase(load_sqlite_settings()) as database:
    async with database.connection() as connection:
        async with connection.execute(
            "SELECT * FROM ingestion_reviews WHERE message_id=?", (message_id,),
        ) as cursor:
            review = await cursor.fetchone()

    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE ingestion_reviews SET reviewer_note=?, updated_at=? WHERE message_id=?",
            (note, updated_at, message_id),
        )
```

`connection()` 独占访问且采用自动提交，适合查询；多条写入使用 `transaction()`，以 `BEGIN IMMEDIATE` 开始，成功提交、异常或取消回滚。事务内使用返回的同一连接，不嵌套申请连接或事务，不手工关闭受管理连接。

`close()` 等待在途上下文结束后释放连接，可重复调用；关闭后不能重新打开该实例。应用启动创建并初始化 `application.state.sqlite_database`，退出时先停止两类人工审核恢复器，再停止消费者和会话服务，最后关闭 SQLite。

---

## 初始化与迁移边界

建表执行来源为 [sqlite_schema.sql](../../../app/infrastructure/sqlite_schema.sql)。当前 schema 版本为 `PRAGMA user_version=8`。空版本数据库在一个事务中创建审核表与索引；重复初始化不清空数据。版本 1 在事务中补充消费 offset、反馈消息 ID 及资源唯一关联索引；已有重复资源关联时升级失败并回滚，不删除记录。版本 2 升级时补充入库文件快照、摘要向量、推荐列表和 pending 源身份唯一索引；不会自动读取或修改 MySQL。版本 3 升级时为删除表补充通行证和正式原文件名，移除不属于删除消息的来源审核员字段。旧来源审核员含非空历史值时明确中止，事务回滚，不静默丢弃数据。版本 4 升级时为入库表补充 `decision` 与最终 `passport`，既有记录默认 NULL，不重新发布历史结果。版本 5 升级时为删除表补充 `decision`，既有结论保持 NULL，保留全部历史且不自动重新发布。版本 6 升级时在一个事务中重建两表，将旧通行证转为 JSON 数组文本并放宽旧整列长度约束，保留原值、记录、索引和自增进度；值与升级规则见[通行证数组契约](../../architecture/data/passport.md)。版本 7 升级时在一个事务中为删除表增加 `name`、`abstract`、`uploader` 快照列，仅按资源唯一 ID、平台及源 ID 一致的已批准 SQLite 入库记录补齐旧名称和摘要；旧上传人未知则保持 NULL，不访问 MySQL，也不重置决定、反馈或审核时间。未知版本明确报错，不自动降级。后续结构修改需要相应版本迁移，不能仅修改 `CREATE TABLE IF NOT EXISTS` 期望更新旧表。

应用启动初始化 SQLite，不自动修改 MySQL。`SQLiteReviewRepository` 保存入库审核申请、自动拒绝意见和已发布反馈凭据；`SQLiteDeletionReviewRepository` 保存无附件删除申请、正式文件引用、人工决定和反馈发布凭据；消费 ACK 前必须保存完整待审核申请，当前申请不提前写入 MySQL。MySQL 仅维护最终版本初始化定义，不再提供旧审核字段的迁移工具；边界见[资源表说明](../../architecture/data/resource.md#初始化与版本边界)。

备份运行中的数据库使用 SQLite 在线备份或先关闭全部连接后复制，不能忽略仍在使用的 WAL 文件。容器重建继续挂载同一个数据卷。旧 `data/registry.db` 的位置调整使用[审核存储迁移命令](review-file-storage.md#旧目录迁移)，不直接移动活动数据库主文件。

---

## 验证

```bash
python -m unittest discover -s tests -p 'test_sqlite.py'
```

测试数据库全部位于 `tests/tmp/`，结束后清理；覆盖重复初始化、运行参数、两张审核表、提交与回滚、取消回滚、并发事务隔离、消息唯一性、状态与备注约束、可空资源关联、完整 uint64 资源 ID、版本拒绝以及应用启停。
