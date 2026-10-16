# 人工删除审核服务

`DeletionReviewService` 负责删除申请的最终决定、正式资源移除、原件保留和结果发布恢复。对外契约见[文件删除审核 API](../../api/deletion-review.md)，字段和关联见[审核表](../../architecture/data/review.md)。后台[删除消费者](deletion-consumer.md)仅接收申请及自动拒绝，人工执行由本服务负责。

---

## 装配与依赖

应用始终创建 `app.state.deletion_review_service`，共享 SQLite 删除仓库、MySQL 仓库、`ReviewFileService` 和中间件平台会话；关闭删除消费也可以审核已有申请。无需模型服务或新增配置。

中间件配置完整时启动 `deletion-review-recovery`，使用已有 `MIDDLEWARE_RETRY_INTERVAL_SECONDS` 扫描未完成决定，发布总超时采用 `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS`。依赖暂不可用不阻塞 Web 启动。恢复器最后启动、最先关闭，先等待在途发布结束，再释放消费者、数据库和 HTTP 客户端。最终审核员由共享人员认证依赖注入。

---

## 决定与执行

批准前检查有效中间件会话、申请绑定的正式资源身份以及原件可读性，避免明显失效的目标进入执行。SQLite 先持久化 `decision`、审核人、意见和首次审核时间，再执行后续阶段；待审列表排除已确定决定，防止重新分配。

批准删除按以下顺序执行：

1. MySQL 事务按申请保存的 `resource_id` 锁定正式行，核对平台、source_id、文件名与原通行证全集（忽略顺序与重复项），执行尚未提交的 DELETE。
2. 使用 `retain_deleted` 将原件从 resource 转入 deletion 保留目录，再读取校验保留原件；失败则回滚 MySQL。
3. 文件保留成功后提交 MySQL，SQLite 更新 `status='approved'`，保留原 resource_id 及申请通行证作为历史关联，保留名称、摘要和原上传人快照。
4. 向中间件发布 `approved=true`、原 request_id、目标平台、人工意见及登录审核员，成功返回 201 后持久化结果消息 ID。

拒绝先保存 SQLite `status='rejected'`，随后发布 `approved=false`；不访问或修改 MySQL，不移动原件。两条分支均先完成本地存储，再发布外部结果。未保存发布凭据时不返回成功。

文件保留使用幂等移动，不覆盖不同内容，暂不启用自动清理。完整目录和路径校验由[审核文件存储](../infrastructure/review-file-storage.md)维护。

---

## 历史查询与原入库审核人

`SQLiteDeletionReviewRepository.list_history` 按删除审核员或文件原入库审核员精确匹配姓名，省略查询姓名时使用当前登录人员。使用删除记录的 resource_id 关联 SQLite ingestion_reviews 中已批准的入库记录，读取其 reviewer；无需为删除表增加重复人员字段，也不依赖已被删除的 MySQL 行。

待审列表与历史均直接返回删除表保存的 `name`、`abstract`、`uploader`，不关联 MySQL 获取文件描述；批准删除后继续保留，历史未知字段为 null。旧记录补齐边界见[审核表契约](../../architecture/data/review.md#删除审核表)。

两个身份同时匹配只返回一次，按删除审核时间及 ID 倒序，仍排除 pending。响应 reviewer 保留实际删除审核员，不因可见范围扩大而改写人员或审核意见；源平台提交人、申请人不参与匹配。仅有同平台同 source_id 的其他资源不扩大可见范围，原关联或原审核员未知时不猜测历史身份。接口语义见[删除审核历史](../../api/deletion-review.md#按审核员获取删除审核历史)。

---

## 重试与恢复

| 中断位置 | 恢复行为 |
| --- | --- |
| 决定保存前资源缺失或引用失效 | 返回冲突，申请仍待审。 |
| 预检后事务发现目标身份变化 | 未移动文件，回滚 MySQL，释放该人工批准预留，恢复待审。 |
| 保留文件失败或 SQL 提交失败 | 保留人工决定，SQL 回滚；重试继续幂等移动，已存在的同内容保留文件可复用。 |
| MySQL 已提交、SQLite 尚未完成 | 旧资源 ID 不存在时，必须原正式路径已无文件且保留原件可读，才完成 SQLite；不会按 source_id 删除后来入库的新资源。 |
| SQLite 已完成、反馈尚未确认 | 只重试结果发布，不重复删除或移动原件。 |
| 已保存发布凭据 | 相同审核员、决定和意见返回原结果，不要求当前平台令牌可用。 |
| 自动拒绝或升级前历史结论 | decision 保持 NULL，不由人工恢复器重新发布，不允许改写历史。 |

按申请 ID 的进程内锁协调人工请求和后台恢复；SQLite 原子预留决定，拒绝不同决定、意见或审核员覆盖。消费者遇到人工 `decision` 只恢复原申请 ACK，不再查询已删除目标或以自动审核员重复反馈。

发布与记录凭据在受保护任务中执行，调用方取消时等待该任务完成再退出；401 只失效本次使用的中间件令牌，人员登录保持独立。发布成功但凭据写入失败仍可能产生跨系统不确定窗口，中间件 409 不包含旧结果正文，不能按成功处理。

> **运行边界：** 文件、MySQL、SQLite 与消息系统没有统一事务；当前单实例、单 worker 运行，不承诺多进程互斥或端到端恰好一次。历史 file_url 无法证明正式身份时需要核对，不猜测路径，不清除未确认决定。

---

## 验证

```bash
python -m unittest tests.test_deletion_review_api tests.test_deletion_consumer tests.test_sqlite
REGISTRY_TEST_MYSQL_PORT=临时容器端口 python -m unittest tests.test_resource_mysql
```

接口测试在 `tests/tmp` 隔离 SQLite、人员和全部文件目录，使用模拟中间件验证本地完成后发布、可信审核员、不可变决定、重复请求、历史排序及故障恢复；验证原入库审核员在正式资源删除后仍能查询删除历史，两个身份同时匹配不重复，并排除申请人、未批准入库及同源不同资源的误匹配。独立 MySQL 集成覆盖真实删除提交、文件失败回滚、取消回滚、身份变化保护和提交后 SQLite 失败恢复；不操作开发业务数据。
