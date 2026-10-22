# 人工入库审核服务

本服务将 SQLite 待审申请转为最终审核结果，协调原件、MySQL 正式资源与中间件反馈。HTTP 契约见[入库审核 API](../../api/ingestion-review.md)，数据字段见[审核表](../../architecture/data/review.md)。

审核历史由 `SQLiteReviewRepository.list_history` 按最终审核员精确读取本地已完成的通过和拒绝记录，默认使用登录姓名，也可指定审核员。保留完整申请快照及最终结论，不依赖 MySQL 资源是否仍存在，也不要求反馈已发布；申请人可由已有上游发起人明确映射，其他旧缺失字段保持 NULL。待审记录和尚未完成本地存储的预留决定不进入历史。接口定义见[审核历史查询](../../api/ingestion-review.md#按审核员获取入库审核历史)。

---

## 处理阶段

`IngestionReviewService` 由应用生命周期装配，始终使用与后台消费者共用的 SQLite、资源仓库和文件服务。列表与待审附件读取不要求中间件在线；初次提交审核决定要求有效平台会话，审核身份来自人员认证。

当前上游消息 `reviewer` 表示来源平台发起入库的人，注册中心统一映射为 `applicant`；对外不再返回重复的 `source_reviewer`。注册中心最终 `reviewer` 仅在实际审核后确定。旧待审记录首次确定决定时补齐已知申请人，再复制到正式资源；身份兼容规则见[审核表契约](../../architecture/data/review.md#入库审核表)。

1. 读取 SQLite 申请，确认待审状态；通过时先确认暂存原件有效。
2. SQLite 写事务预留 `decision`、实际审核人、意见、审核时间和最终 `passport`。未指定通行证时生成 UUID 十六进制字符串的单元素数组，并只生成一次；指定时保存完整数组，拒绝保存空数组，见[通行证数组契约](../../architecture/data/passport.md)。
3. 通过：在 MySQL 事务中锁定平台文件名与源 ID，创建或校验完整快照一致的正式记录；执行 `promote_ingestion` 并校验正式原件后提交事务。
4. 拒绝：不调用 MySQL 写入或文件迁移，原件仍保留在 ingestion 暂存目录。
5. SQLite 更新 `status`，通过时关联正式 `resource_id`，拒绝时保持 NULL。
6. 调用中间件通用入库结果发布方法，保存 `result_message_id` 后返回最终响应。

`status='pending' AND decision IS NOT NULL` 表示决定已确定、存储尚未完成，不再出现在待审核列表。正式资源处理完成但反馈未发布时，`status` 已为最终状态，`result_message_id` 仍为空；据此只恢复发布。

批准时必须提交严格整数 `file_level`，先在 SQLite 冻结，再与其他审核快照一同进入 MySQL。恢复和重复提交使用原等级，修改等级会产生 409。历史已批准但尚未反馈且等级为 NULL 的记录停止自动处理，需要先明确补齐等级；不自动选择默认等级。

拒绝分支本地等级为 NULL，中间件所有新结果都必填等级，所以人工和自动拒绝发布时固定携带 `file_level=0`；这只是拒绝消息的协议占位值，不给文件设置生效等级。

原有自动拒绝继续使用独立的消费恢复路径；升级前已完成记录的 `decision` 默认为 NULL，不重新发布历史结果。人工决定记录的消息重投直接恢复 ACK，不重新运行智能体或自动拒绝发布。

---

## 并发与恢复

同一进程内使用按审核 ID 的异步锁串行化人工提交、待审附件读取及恢复器。SQLite `BEGIN IMMEDIATE` 确保预留决定不能被不同意见覆盖；MySQL 唯一平台附件键与源身份键阻止正式资源重复建档。此前 MySQL 已提交但 SQLite 关联未保存时，仅复用全部入库快照一致的记录；差异视为冲突，不覆盖。

MySQL 记录仅在文件完整晋升后提交。数据库回滚不能撤销已经完成的文件迁移，因此重试通过文件转移的幂等能力恢复；SQLite 阶段记录保留该申请，不丢失关联。正式身份冲突在晋升前检出时，回滚 MySQL 并释放批准预留，让该申请重新可审核。

应用启动后自动扫描 `decision IS NOT NULL AND result_message_id IS NULL`，依次恢复未完成决定，循环间隔沿用 `MIDDLEWARE_RETRY_INTERVAL_SECONDS`。只在配置中间件时启动恢复器，HTTP 发布总超时沿用 `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS`。取消时等待有界发布及回执写入，退出时先停止恢复器，再关闭客户端、数据库和会话。

中间件 401 只失效本次使用的服务器令牌，不影响本平台人员 token。409 不证明旧结果内容一致，不作为成功；本地保留未完成记录供核对。HTTP 失败不删除原件、不撤销已完成正式资源，不将读取失败伪装成空列表。

MySQL、SQLite、文件系统和消息中间件不构成统一事务。进程崩溃发生在上游已发布但 SQLite 尚未保存回执时，不能保证跨系统恰好一次，需核对上游结果。当前部署使用单 Web 进程，反馈阶段的进程内锁不提供多进程发布互斥；扩展为多进程前需要增加发布阶段的持久化租约或上游幂等确认契约。

---

## 原件读取与保留

待审接口按 SQLite 审核 ID 定位平台与附件名，仅使用 `ReviewFileService.read_ingestion`，不查询 MySQL，也不回退到正式或删除保留目录。决定确定后拒绝继续通过待审接口读取；已批准原件使用返回的 `resource_id` 访问[正式原件接口](../../api/resource.md#按-id-获取正式文件原件)。

两个原件接口共用后缀媒体类型推断、未知类型二进制回退和安全的文件读取能力，均不渲染、转码或解压。大小限制继续使用 `INGESTION_MAX_FILE_BYTES`。拒绝附件暂不自动删除，删除保留文件同样没有自动清理。

---

## 验证

```bash
python -m unittest tests.test_ingestion_review_api tests.test_sqlite tests.test_review_repository
```

所有测试数据库与原件位于 `tests/tmp`。覆盖登录身份、待审字段、推荐列表、不同文件类型、通过与拒绝、重复和相反决定、文件迁移失败、MySQL 提交后的 SQLite 失败、反馈失败恢复、后台恢复、错误脱敏及 SQLite 升级。隔离 MySQL 集成测试验证实际正式字段、事务回滚、源身份冲突与重试复用。
