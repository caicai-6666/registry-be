# MyVector 与 vLLM 向量检索试用

本模块验证使用 LlamaIndex 调用 vLLM embedding，并通过 MyVector 在 MySQL 内完成文件文本的相似度匹配。开发 MySQL 已加载插件；[入库工作流](../../architecture/workflow/ingestion/readme.md)从正式 `resources` 表筛选候选并同步 HNSW 缓存。完整工作流已接入[后台接收服务](../application/ingestion-consumer.md)，后台自动调用摘要向量模型及判重、通行证推荐智能体。

---

## 模块与配置

| 模块 | 职责 |
| --- | --- |
| `app/config/embedding.py` | 读取模型地址、名称、预期维度、密钥及超时。 |
| `app/service/embedding.py` | 提供异步文本向量生成，检查维度、有限数值与非零向量。 |
| `app/infrastructure/myvector.py` | 自定义 LlamaIndex 向量库，支持异步节点写入、HNSW 检索和文档删除。 |
| `scripts/myvector_trial.py` | 启动独立容器，验证真实模型、检索、HNSW 与过滤能力，结束后清理。 |
| `knowledge-accumulation-deploy/mysql/init/002-myvector-documents.sql` | 索引缓存建表定义，由部署初始化或显式迁移创建。 |

进程环境优先于根目录 `.env`，不展开环境变量引用。

| 配置项 | 用途 |
| --- | --- |
| `VLLM_EMBEDDING_PROVIDER` | 固定为 `vllm`，其他提供方当前不支持。 |
| `VLLM_EMBEDDING_ENDPOINT` | 固定为 `embeddings`，对应 `/v1/embeddings`。 |
| `VLLM_EMBEDDING_BASE_URL` | OpenAI 兼容接口地址，支持服务根地址或 `/v1` 地址。 |
| `VLLM_EMBEDDING_MODEL` | vLLM 发布的模型名称。 |
| `VLLM_EMBEDDING_DIMENSIONS` | 预期维度，范围 1 至 4096；实际返回不符时拒绝保存。 |
| `VLLM_EMBEDDING_API_KEY` | 可选密钥；无需认证时留空。 |
| `VLLM_EMBEDDING_TIMEOUT_SECONDS` | 请求超时，默认 60 秒。 |
| `VLLM_EMBEDDING_BATCH_SIZE` | 每批文本上限，默认 32。 |
| `VLLM_EMBEDDING_MAX_CONCURRENT_REQUESTS` | 每个模型实例同时进行的异步请求上限，默认 10；单条、批量及查询共享限制。 |
| `VLLM_EMBEDDING_NORMALIZE` | 是否在客户端执行 L2 归一化，默认 `true`。 |

本次模型为 `qwen3-vl-embedding-8b`，真实接口返回 4096 维向量。地址和凭据仅维护在本地配置，文档与试用报告不记录凭据。

使用基于 `OpenAILikeEmbedding` 的 `VLLMEmbedding` 接受 vLLM 自定义模型名；由同一适配器完成文档、批量和查询向量的校验及归一化。只支持异步调用，批量生成使用 `EmbeddingService.embed_texts`。没有使用全局 `Settings.embed_model`，调用方通过实例显式注入模型；异步调用完成后应关闭 `EmbeddingService`。请求使用 float 编码，当前不向模型传入维度裁剪参数。

---

## 固定摘要相似检索指令

所有正式文本、批量与查询向量统一采用：

> 检索与这份摘要语义相关的文本。

唯一代码来源为 `app/service/embedding.py` 的 `SUMMARY_EMBEDDING_INSTRUCTION`，不通过环境变量添加第二份配置。LlamaIndex 仍负责异步 embedding 接口及批量调度；适配器使用共享 httpx 客户端调用 vLLM `/v1/embeddings` 的批量 messages 协议：每条独立 system 放固定指令，user 仅放摘要原文，`add_generation_prompt=true`、`add_special_tokens=false`、`encoding_format=float`。单条和查询也走同一请求契约，不使用普通 input 或手工拼接模型聊天模板。

返回必须为 200，条目 index 必须完整、唯一且与输入一一对应；按 index 恢复顺序，再验证维度、有限非零向量并按配置归一化。空摘要在请求前拒绝。单条、批量、查询共享并发限制；临时网络故障及 429/500/502/503/504 最多重试两次，凭据错误、输入错误和非法响应不重试。HTTP 客户端由 EmbeddingService 统一关闭。

### 已有向量的兼容处理

正式资源和旧审核快照没有存储向量指令标识，不能直接假定同名模型、同维度就兼容。应用组合根为 `ReadyResourceSearch` 注入同一个 EmbeddingService；每次加载正式候选时，用保存的摘要按当前指令生成检索向量，再同步 HNSW，而不直接使用旧库向量。缓存键包含模型、维度、固定指令和摘要哈希，摘要修改后重新生成；最多保留 10000 条进程内向量，重启后按需重建，首次检索会增加摘要编码请求与延迟。

只在内存和既有可重建 HNSW 缓存使用新向量，不批量改写 MySQL 正式 embedding、SQLite 历史审核向量或推荐结果。所有候选摘要必须可用；缺少摘要、模型失败或向量无效时，在替换索引快照之前失败，不混用旧向量或静默跳过文件。新申请的向量由统一指令生成后按原流程保存；旧申请重放仍保持历史结果。

离线调用者可以不注入 EmbeddingService，使用自行保证同一编码契约的预计算向量；正式入口必须注入。指令不含版本号或工程标识，缓存标识保留在模型上下文外。

该文本专用表述由用户最终确定，之前含“图像或文本”的指令实验不能直接视为其效果证明。当前相似度阈值不在本次修改中自动调整；输入契约变化后仍需按真实标签验证判重与关联阈值。

---

## LlamaIndex 对接与存储

`MyVectorStore` 实现 LlamaIndex 的 `BasePydanticVectorStore`：

- `async_add`：检查向量并事务写入，使用插件的 `myvector_construct` 编码。
- `aquery`：默认通过四参数 `myvector_ann_set` 获取 HNSW 候选，使用同一 SQL 中的 `myvector_row_distance(id)` 读取距离，返回相似度 `1 - distance`。
- `adelete`：按文档标识和模型删除节点；平台快照刷新可调用 `adelete_platform`，全平台正式资源快照可调用 `adelete_status("ready")` 清理当前模型的对应缓存。
- 同步方法明确拒绝调用，使用 `VectorStoreIndex.ainsert_nodes` 与 `retriever.aretrieve`。

试用库中的 `myvector_documents` 保存节点内容、模型名称、维度、平台、审核状态和 BLOB 向量。模型名称、维度作为检索条件，避免跨模型或跨维度比较；同名模型权重变化时仍需重新生成向量。`ReadyResourceSearch.search` 在去重检索前以正式资源表为准刷新对应平台快照；`search_all_platforms` 为[通行证推荐子图](../../architecture/workflow/passport-recommendation/readme.md)刷新全平台有通行证的正式文件快照，沿用同一 HNSW 封装及实例锁。正式资源表只读，缓存更新需要写入和索引管理权限；直接使用适配器的调用方仍需自行管理节点与业务状态。

正式 `resources` 已移除 status，表中存在即表示已入库。索引缓存的兼容字段 `status` 固定为 ready，不代表正式表仍维护审核状态。

适配器支持 `source_platform`、`status` 的相等条件，组合方式为 AND。`top_k` 范围 1 至 100；不支持混合检索、稀疏检索、嵌套过滤或其他字段。平台与审核状态作为元数据，不加入试用文件的 embedding 文本。

适配器默认 `search_mode="hnsw"`，也可显式选择 `exact` 作为精确检索基线。HNSW 先取至少 100 条或 `10 × top_k` 的候选，再筛选；不足时将候选数翻倍，最多取 10,000 条。达到候选上限后仍不足时以精确距离查询兜底；全部记录已进入候选时，返回实际满足条件的结果，不虚构数量。

新增、更新或删除节点会使索引失效，下一次查询前重建并检查类型、维度和距离配置；首次查询也会重建。操作由实例内锁串行协调。一个索引只能容纳同一维度的向量；当前试用 SQL 配置为 4096 维 HNSW/Cosine，容量 10,000。调用方修改模型或维度前须调整部署 SQL、重新生成向量与重建索引。

数据库连接池由调用方提供并关闭，须使用 `autocommit=True`；适配器不创建表。插件内部的 `myvector.cnf` 连接与可写索引目录必须配置，调用方须具有索引管理存储过程的执行权限。显式 `rebuild_index()` 可用于外部导入后的重建；当前按单实例写入协调，多实例或外部写入不会自动触发其他实例重建。已接入的 `ReadyResourceSearch` 在每次检索前读取正式资源并刷新当前平台或全平台快照，因此已删除资源不再作为该次检索的候选；缓存不能替代正式资源身份校验。

---

## 可重复验证

在项目根目录执行：

```bash
conda activate registry-be
python -m pip install -r requirement.txt
python -m pip check
python -m unittest discover -s tests
python scripts/myvector_trial.py
```

试用脚本使用固定摘要的 MySQL 8.4 插件镜像，随机容器名、随机密码、仅本机可访问的动态端口和独立数据目录。SQL 从部署项目的 `mysql/init/002-myvector-documents.sql` 读取，不在后端启动时建表。结束时删除自己的容器及匿名卷，正式开发库保持运行。

验证使用四份合成文件文本：采购合同、装修方案、产品介绍和待审核合同。先配置插件内部连接与索引目录，再检查真实 4096 维向量的写入与 HNSW 排序、平台和审核状态筛选，最后验证基础查询和五参数接口的兼容性。另以 250 条较近但不允许的合成记录验证候选扩展、候选上限兜底，以及向量更新、删除后的索引一致性。

每次结果保存至独立的 `experiment/myvector-trial/<运行时间>/result.json`，包含镜像摘要、插件版本、HNSW 检索结果、索引状态及过滤能力检查，不包含接口密钥或数据库密码。历史产物已迁至该目录，保存边界见[测试与实验产物规范](../../standard/experiment.md)。

---

## 实测限制与后续接入

根因已经确认：固定镜像里的 `myvector_ann_set_init` 只允许 3 至 4 个参数，五参数调用会在 UDF 初始化时返回错误 1123。上游 [变更日志](https://github.com/askdba/myvector/blob/main/CHANGELOG.md) 将第五个过滤参数列在 `Unreleased`，不能根据主分支文档推定已发布镜像支持该接口。

适配器使用现有镜像支持的四参数 UDF，并在应用侧扩大候选后筛选，不依赖尚未发布的五参数能力。HNSW 为近似搜索，候选扩展不等于精确召回保证；强筛选到达候选上限时会明确采用精确查询兜底。

小规模合成数据仅验证链路和筛选语义，不能据此判断大规模查询性能、召回率或生产可用性。已验证实例内新增、更新及删除后的重建；多实例写入、重启后大规模加载、备份恢复及真实大规模过滤仍需专项验证。

参考 [MyVector 使用说明](https://github.com/askdba/myvector/blob/main/docs/usage.md)、[已知限制](https://github.com/askdba/myvector/blob/main/docs/LIMITATIONS.md)和 [LlamaIndex 向量库集成列表](https://developers.llamaindex.ai/python/framework/community/integrations/vector_stores/)。上游主分支文档与镜像发布能力可能不同，应以固定镜像的实际验证结果为准。
