# 正式文件等级调整

管理员通过 [catalog 调整接口](../../api/catalog.md#调整正式文件等级)修改已入库文件等级，并向来源平台发布调整消息。文件权限与平台管理权限独立；该接口只允许中央登录有效且具有 registry 管理权的用户调用。

---

## 执行流程

1. 按注册中心文件 ID 查询 MySQL 正式文件，验证来源 ID 存在；当前等级与目标相同时不创建新调整。
2. 在 [SQLite 独立日志](../../architecture/data/file-level-adjustment.md)记录文件身份、调整前后等级、操作人及备注。
3. MySQL 事务通过 `SELECT ... FOR UPDATE` 校验文件身份与等级，只更新 `resources.file_level`，不改入库审核原始记录或删除申请快照。
4. 本地提交后，使用注册中心平台 token 调用中间件 `POST /middleware-service/api/file-permission-adjustments/publish`。
5. 严格校验中间件返回 201、`status=published` 和消息 ID，再将日志标记为发布完成。

中间件消息为 `{source_id,target_platform_code,file_level,note,reviewer}`。文件 ID 和平台从正式文件记录取得，操作人从服务器认证身份取得，客户端不能指定或伪造。

---

## 失败与并发边界

同一进程的调用串行处理，SQLite 部分唯一索引禁止同一文件同时创建多个未完成意图。未完成日志只允许原操作人使用相同等级和备注继续处理；新等级或另一操作人返回 409。发布成功后的相同请求在当前等级仍相同时复用原日志，不重复发布；未发生变化且无匹配日志时返回 409。

网络、MySQL 或 SQLite 故障返回脱敏 503，附 `log_id` 与 `database_updated`。后者为 true 表示本地提交已确认、false 表示本次没有确认更新、null 表示提交结果需要核对。日志保留，重复提交原参数可继续；没有新增后台自动重发任务。重启应用后仍可从日志恢复。

中间件 401 仅失效本次注册中心平台 token；409 表示同一来源文件存在尚未消费的调整，不能当作本次载荷已发布。若发布实际成功但响应或 SQLite 凭据写入失败，需要核对中间件及来源平台：中间件未提供客户端幂等键或按载荷查询结果的契约，不能保证跨系统恰好一次；消费确认后重试可能再次发布同一目标等级。新调整在旧消息未消费时也可能已本地提交但发布返回 409，必须保留待同步状态。

这是单实例管理写入流程。进程锁不协调多个 registry 写实例；SQLite 唯一索引防止多条意图但不能让多个进程安全地同时恢复发布同一日志。多实例部署需要另行实现跨进程执行互斥。MySQL、SQLite 与消息中间件没有分布式事务，本地已完成但发布未确认时不能宣称来源平台等级已同步。

---

## 配置、依赖与验证

复用现有 MySQL、SQLite、中间件会话及 HTTP 超时配置，无新增环境变量、依赖或部署挂载。SQLite 数据目录须持久化。

```bash
python -m unittest tests.test_file_level_adjustment tests.test_management_dependencies
```

测试使用 `tests/tmp` SQLite 和 MockTransport，覆盖严格整数、可信身份、管理员依赖、本地先提交、日志保留、重启重试、并发重复提交、MySQL 身份冲突回滚及中间件错误；不调用真实共享服务或修改业务数据。


## 管理员查看历史

`GET /knowledge-accumulation/registry/api/catalog/file-level-history` 返回所有管理员的等级调整日志，包含未完成意图、本地已更新及发布已确认记录；只查询 SQLite，正式资源删除后仍可查看。沿用中央登录和 registry 管理权限依赖，不按当前 userid 筛选，不触发恢复或消息发布。完整返回值见 [catalog API](../../api/catalog.md#获取文件等级调整历史)。

隔离验证：`python -m unittest tests.test_file_level_history tests.test_management_dependencies`，覆盖跨管理员共享、全部状态、同时间排序、空历史、SQLite/数据校验故障、敏感字段排除及全局管理员依赖。
