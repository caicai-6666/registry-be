# 入库申请接收服务

本模块随 FastAPI 启动，串行执行完整的[入库申请工作流](../../architecture/workflow/ingestion/readme.md)，持续接收中间件申请，为人工审核准备原文件、摘要向量和通行证建议。判重确认重复时自动发布拒绝反馈；不重复时保存 SQLite 待审核申请。

> **确认边界：** ACK 表示申请已持久化，或自动拒绝结果已确认发布，不表示人工审核通过。不重复申请必须先保存本地原文件与完整 SQLite 记录；中间件可能在 ACK 后清理原附件。

---

## 模块职责与依赖

| 模块 | 职责 |
| --- | --- |
| `app/main.py` | 装配运行依赖，注册启动与关闭顺序。 |
| `app/service/ingestion_consumer.py` | 执行完整图、持续消费、保留失败申请并调度重试。 |
| `app/workflow/ingestion/` | 申请处理、智能体调用、拒绝发布、待审保存及 ACK 的业务规则。 |
| `app/service/embedding.py` | 使用 LlamaIndex 异步生成摘要向量。 |
| `app/service/resource_candidates.py` | 复用 MyVector HNSW 完成同平台判重检索及跨平台推荐检索。 |
| `app/service/resource_pdf.py` | 在内存中渲染 PDF，提供共享页面与媒体引用。 |
| `app/infrastructure/mllm.py` | 判重与推荐共用的异步多模态模型客户端。 |
| `app/service/pending_review.py` | 保存原文件与完整 SQLite 待审核申请。 |

每个应用实例装配一个消费服务、一个 MySQL 资源仓库、一个 embedding 服务、一个模型客户端和一个 HNSW 搜索服务。判重与推荐共用模型客户端及其请求配额、共用检索服务及其快照锁；单条申请的文件字节、渲染页与模型对话不长期保存在消费服务中。

SQLite、HTTP 客户端及 MySQL 连接由应用管理，`IngestionConsumerService.close()` 只停止消费任务。FastAPI 停机时先取消并等待主图及子任务退出，再依次关闭模型、embedding、MySQL、消费 HTTP、会话服务和 SQLite；启动中途失败也释放已创建的资源。

应用提供 `app.state.ingestion_consumer_service`、`app.state.ingestion_workflow_context` 和 `app.state.review_file_service`，便于定位后台依赖。消费关闭时前两项为空。MySQL 在工作流首次查询时建立连接，模型首次需要时发起请求，外部网络故障不阻塞 Web 服务启动；必要配置不合法时启动失败。

---

## 持续消费与审核准备

1. 等待[中间件会话](../infrastructure/middleware-session.md)提供有效令牌。
2. 执行一次完整入库图；首次从中间件拉取消息，失败重试时优先复用当前未完成的消息。
3. 图内完成身份检查、内存下载、摘要向量化、HNSW 检索及并发判重。
4. 重复申请保存拒绝审核记录，发布反馈并记录发布凭据后 ACK；不重复申请完成通行证推荐，将原文件与完整申请暂存后 ACK。
5. 确认图返回 `acknowledged=True` 后释放当前消息，立即执行下一次；空队列按轮询间隔等待。

节点行为与图的分支由[入库申请工作流](../../architecture/workflow/ingestion/readme.md)维护。待审数据契约见[审核表](../../architecture/data/review.md)，文件持久化见[待审核文件持久化](pending-review.md)。后台不向 MySQL `resources` 写入待审申请；HNSW 检索会更新可重建的 `myvector_documents` 索引缓存。

默认存储位置：

| 数据 | 位置 |
| --- | --- |
| 审核申请与通行证建议 | `data/review-log/registry.db` 中的 `ingestion_reviews`。 |
| 入库待审原文件 | `data/review-log/tmp-file/ingestion/{source_platform}/{file_url}`。 |
| 已正式入库文件 | `data/resource/{source_platform}/{file_url}`。 |
| 删除后保留文件 | `data/review-log/tmp-file/deletion/{source_platform}/{file_url}`。 |

启动时准备三个文件根目录，即使关闭消费也会准备；平台目录按需创建。正常新申请写入名称、摘要、申请信息、摘要向量及 `{passport, reason}` 推荐列表，状态为 `pending`，`resource_id` 为空，无推荐保存 `[]`。审核员可从 SQLite 获取待审数据，原文件位于入库暂存目录；本模块不提供人工审批接口。

历史消费者生成的待审记录可能没有向量或推荐结果。本次切换不自动回填历史记录；已保存申请重投时仍复用原审核记录，不重置人工结论。

---

## 配置

进程环境优先于根目录 `.env`，修改后重启。沿用 [.env.example](../../../.env.example) 中各独立配置区，不增加第二套模型或检索参数。

| 配置 | 用途 |
| --- | --- |
| `INGESTION_ENABLED` | 默认 `true`，启用后台完整入库图。 |
| `INGESTION_POLL_INTERVAL_SECONDS` | 默认 `2` 秒，无令牌或队列为空时等待。 |
| `INGESTION_RETRY_INTERVAL_SECONDS` | 默认 `5` 秒，失败申请重试间隔。 |
| `INGESTION_DOWNLOAD_TIMEOUT_SECONDS`、`INGESTION_MAX_FILE_BYTES` | 内存下载超时与文件大小上限。 |
| `INGESTION_DUPLICATE_*`、`INGESTION_COMPARISON_CONCURRENCY` | 判重候选搜索及文件比较并发。 |
| `RESOURCE_ROOT`、`REVIEW_LOG_ROOT`、`SQLITE_PATH` | 正式资源、审核附件及审核数据库的位置。 |
| `VLLM_EMBEDDING_*` | 摘要向量模型，详见[向量检索](../infrastructure/myvector.md)。 |
| `VLLM_MLLM_*`、`FILE_DEDUPLICATION_*` | 模型生成、思考、页面预算及判重循环配置，详见[多模态模型服务](../infrastructure/mllm.md)与[判重子图](../../architecture/workflow/file-deduplication/readme.md)。 |
| `PASSPORT_RECOMMENDATION_*` | 推荐搜索独立阈值与 Top-K，详见[推荐子图](../../architecture/workflow/passport-recommendation/readme.md)。 |

中间件未配置地址或密钥时不创建消费依赖；入库消费开关为 `false` 时不创建入库消费、embedding 或 MLLM。若独立启用[删除消费](deletion-consumer.md)，仍创建共享 HTTP。应用始终准备正式资源仓库以供[文件列表查询](../../api/catalog.md)；没有消费时，首次查询才加载 MySQL 配置并建立连接。消费启用时需具备有效 MySQL、embedding 和 MLLM 配置。MySQL 表及 MyVector 插件由部署项目初始化，后端不执行 MySQL 建表。

pull、publish 和 ACK 的请求总超时沿用 `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS`；PDF 下载及模型请求使用各自的超时。部署时持久化整个 `/workspace/data`，运行方式见[MySQL 与统一部署](../infrastructure/mysql.md)。

---

## 失败与恢复

| 情况 | 行为 |
| --- | --- |
| 下载、向量化、检索或智能体输出失败 | 不 ACK，保留当前申请；等待后从干净状态重新执行，不能以未知结论放行。 |
| 原文件已保存、SQLite 提交失败 | 保留完整文件；重试时重新下载并完成前置判断，持久化时核对内容、补齐申请。 |
| SQLite 已保存，ACK 临时失败或响应丢失 | 复用已保存记录与原文件，不重复模型调用、推荐或建记录。 |
| ACK 返回 409 | 释放当前消息指针，重新 pull 恢复中间件队首；原审核记录保留。 |
| 拒绝 publish 返回 409 | 不视为成功，不清除当前消息，不 ACK；继续保留拒绝记录等待恢复。 |
| 中间件操作返回 401 | 对该操作实际使用的令牌执行失效处理，等待会话重新登录，重试使用最新令牌。 |
| 拒绝反馈已发布且 SQLite 已记录凭据 | 重投只恢复 ACK，不再次调用智能体或发布反馈。 |
| 已保存原文件丢失 | 尝试重新下载补回文件，不重复智能体；上游已清理时无法自动恢复。 |
| 进程重启 | 重新 pull；通过 SQLite 消息身份、审核状态与发布凭据恢复已提交申请。 |
| 应用关闭 | 取消并等待图退出；内存下载不留下半文件，落盘操作等待线程结束并清理临时文件。 |

文件、SQLite 和中间件没有跨系统事务。特别是拒绝反馈已成功发布、但发布凭据尚未保存的窗口仍需核对，不能凭 409 承诺成功；不保证端到端恰好一次。尚未提交的申请在重启后会重新计算向量与模型判断。

非法队首、缺失摘要、无效模型输出均不自动跳过，不推进消费进度。当前部署按单实例、单 worker 使用，不支持多个消费者同时协调队首。日志仅记录阶段、异常类型、HTTP 状态及安全消息标识，不记录凭据或业务原文；HTTP 状态同时识别中间件 HTTPX 异常与模型 SDK 的状态异常，例如 NotFoundError 对应 404。模型返回 409 不触发中间件 ACK 冲突恢复。未启用历史附件自动清理。

---

## 验证与实现边界

```bash
conda activate registry-be
python -m unittest tests.test_ingestion_consumer tests.test_ingestion_lifespan tests.test_source_identity
```

测试使用隔离 HTTP 和模型依赖，真实 SQLite 及文件目录位于 `tests/tmp`，结束后清理。覆盖完整后台图、判重拒绝、正向通行证推荐保存、模型故障、SQLite 故障、重投、ACK 重试及 409、401 换令牌、进程重启、请求总超时、停机取消和启动中途失败的资源清理。模型协议与真实 MyVector 集成由各自模块测试维护。

已实现从后台消费到自动拒绝或人工待审准备。[人工入库审核服务](ingestion-review.md)已提供列表、待审原件读取与审批，批准后写入 MySQL 并转移文件。入库消费者遇到已预留的人工决定直接恢复 ACK，不再判重、补文件或自动反馈。删除消费已独立接入[删除申请接收服务](deletion-consumer.md)；推荐质量和判重准确率需继续通过真实样本评估。
