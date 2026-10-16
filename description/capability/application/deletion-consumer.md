# 删除申请接收服务

`DeletionConsumerService` 随 FastAPI 启动，持续拉取无附件删除申请，核对正式资源与完整通行证集合，保存 SQLite 待审核记录；不存在或无效的删除目标形成自动拒绝并反馈。接收期间不删除 MySQL 资源、不下载或移动文件。

> **确认边界：** 正常分支在保存待审核申请后 ACK；自动拒绝分支在保存拒绝记录、确认反馈发布并记录发布凭据后 ACK。ACK 不表示人工审核通过，也不表示删除已执行。

---

## 模块与依赖

| 模块 | 职责 |
| --- | --- |
| `app/model/deletion.py` | 无附件申请、消费确认与正式资源定位结果的类型校验。 |
| `app/config/deletion.py` | 独立的删除消费开关、轮询和重试周期。 |
| `app/infrastructure/middleware.py` | 删除申请 pull/ack 与批准、拒绝结果 publish。 |
| `app/repository/resource.py` | 按平台码与 source_id 只读查找正式资源。 |
| `app/repository/deletion_review.py` | 删除申请幂等写入、资源唯一绑定及发布凭据。 |
| `app/service/deletion_consumer.py` | 连续消费、身份核对、文件存在性检查、拒绝发布及恢复。 |
| `app/main.py` | 组合根装配、生命周期与共享依赖清理。 |

入库与删除各自串行消费独立的消息流，共用中间件认证会话、业务 HTTP 客户端、MySQL 仓库和 SQLite 基础设施。两条流的当前消息、ACK 和重试状态互不混用。删除服务不依赖 embedding、MLLM 或入库工作流，关闭入库消费仍可独立启用删除消费。

服务入口为 `app.state.deletion_consumer_service`。关闭时先停止人工审核恢复器，再取消并等待两个消费者退出，再释放共享连接；启动失败也关闭已分配资源。MySQL 网络连接在首次查询时建立，暂时不可用不阻塞 Web 启动。当前按单实例、单 worker 部署，不协调多个注册中心消费者。

---

## 接收流程

1. 等待有效中间件令牌，调用 `POST /middleware-service/api/deletion-requests/pull`；请求无 Body，204 时按轮询间隔等待。
2. 使用 SQLite 消息唯一键查询已有审核；存在人工 decision 时只恢复 ACK，不再核对可能已移除的正式目标，不重复自动反馈。自动拒绝重投复用旧意见与发布凭据，历史已批准重投仅恢复 ACK，不恢复已经删除的文件。
3. 新申请按 `(platform_code, source_id)` 精确查 MySQL `resources`，不能仅凭不唯一的 passport 或业务文件名定位。发现多个正式资源时停止本次处理并等待重试及核对，不任选目标。
4. 无正式资源时自动拒绝；有资源但 passport 全集不一致时自动拒绝，集合比较忽略顺序与重复项，不接受子集或额外值。数据库异常不等同于资源不存在。
5. 校验正式资源原文件位于 `{RESOURCE_ROOT}/{source_platform}/{file_url}`；文件缺失、非法路径或符号链接停止处理，不拒绝、不 ACK，等待存储问题恢复。
6. 保存 `deletion_reviews`，状态 `pending`，关联 `resource_id`，`file_url` 保存 MySQL 中的原文件名，同时保存消息中的名称、摘要与原上传人快照。之后 ACK，只提交本次删除申请的 message_id 并核对返回 offset。

passport 消费统一为数组，历史单字符串仅在消息边界兼容；存储与完整集合核对见[通行证数组契约](../../architecture/data/passport.md)。

新申请的 name、abstract、uploader 是业务描述，在待审与自动拒绝记录中保存为首次接收快照，不作为文件路径。审核者从 SQLite 获取描述、申请人及其备注，资源删除后仍能查看文件基本信息。字段权威定义见[审核表契约](../../architecture/data/review.md)。

```text
pull → 查已有审核 → 按平台与源 ID 查询资源
  ├─ 不存在 / 通行证不符 → SQLite rejected → publish approved=false → 保存发布凭据 → ACK
  └─ 存在且通行证一致 → 核对 resource 原文件 → SQLite pending → ACK
```

删除申请读取无需附件下载接口。历史消息中的额外 file 字段不会用于文件定位；缺少当前必须的业务字段时仍不能消费，不猜测历史身份。

---

## 拒绝反馈与唯一关联

自动拒绝发布到 `POST /middleware-service/api/deletion-results/publish`，发送 `request_id`、`target_platform_code`、`approved=false`、Markdown `note` 和固定审核员“注册中心自动审核员”。只接受 `201 published`；反馈 message_id 持久化后才 ACK。

沿用现有删除表的 `resource_id` 唯一约束：同一正式资源只绑定一条删除审核记录。不同消息再次申请已绑定资源时，不覆盖原记录，形成独立的自动拒绝记录；该拒绝的 `resource_id` 和 `file_url` 为 NULL，仍保留平台、source_id、申请通行证及拒绝意见。此约束也适用于此前已审核的绑定记录；若后续允许同一资源多次人工删除审核，应先调整表关联契约。

不存在、通行证不符的自动拒绝也不绑定资源、不猜测文件名。人工拒绝与批准通过[人工删除审核服务](deletion-review.md)执行，不由消费者代替。

---

## 重试与故障恢复

| 情况 | 行为 |
| --- | --- |
| MySQL、SQLite、文件检查或消息校验失败 | 不 ACK，不修改正式资源，保留当前消息等待重试。 |
| 待审记录已保存但 ACK 失败 | 幂等复用审核记录，重新核对目标与原文件，再重试 ACK。 |
| 已保存待审申请的目标变化或文件缺失 | 不转换为自动拒绝，不覆盖旧申请，不 ACK，等待人工核对。 |
| 拒绝 publish 失败 | 保留 rejected 记录，未持久化成功凭据前不 ACK。 |
| 拒绝反馈已发布且凭据已保存 | 重试或重启后复用凭据，不重复 publish。 |
| ACK 返回 409 | 清除当前消息指针，重新 pull 恢复中间件队首。 |
| publish 返回 409 | 不能证明旧反馈内容相同，保留当前消息，不将其视为成功。 |
| 401 | 仅失效该请求使用的令牌，等待会话重新登录；后续请求读取最新令牌。 |
| 重启 | 按 message_id、offset、平台、源 ID、通行证、申请人、备注和申请时间核对持久化记录。 |

不自动跳过非法队首。结果流与 SQLite 没有跨系统事务；发布成功但凭据未保存的窗口仍需核对，不能承诺端到端恰好一次。名称、摘要及原上传人不作为本地去重身份；重投保留首次快照，不刷新为后来消息中的描述。

待审正式原文件保留在 resource，不提前移动到 deletion。人工最终批准后转入保留目录，具体执行见[人工删除审核服务](deletion-review.md)，保留目录不自动清理。

---

## 配置与验证

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DELETION_ENABLED` | `true` | 独立启用删除后台消费。 |
| `DELETION_POLL_INTERVAL_SECONDS` | `2` | 无消息或无令牌时等待秒数。 |
| `DELETION_RETRY_INTERVAL_SECONDS` | `5` | 处理失败后等待秒数。 |
| `RESOURCE_ROOT` | `data/resource` | 正式文件引用的读取根目录。 |
| `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS` | `40` | pull、publish 和 ACK 的请求总超时。 |

沿用已有 MySQL 与 SQLite 配置，不新增附件目录。中间件未配置或消费关闭时不创建删除消费者；删除独立启用时需有效 MySQL 配置，不要求模型配置。进程环境优先于 `.env`，修改后重启。

```bash
python -m unittest tests.test_deletion_consumer tests.test_deletion_lifespan
```

隔离验证覆盖无附件 HTTP 契约、正常暂存、缺失资源、通行证不符、重复绑定、数据库和文件故障、SQLite 失败、拒绝发布及凭据保存失败、重投、ACK 503/409/401、身份校验、停机取消和独立启动。SQLite 与文件产物位于 `tests/tmp` 并在结束后清理。真实 MySQL 集成使用独立容器及仅 SELECT 权限账号，验证跨平台隔离、正式文件引用和不写入资源表。
