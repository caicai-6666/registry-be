# vLLM 多模态模型接入

> **用途：** 提供异步 Chat Completions、PDF 页面内存输入、媒体 UUID 复用、共享请求并发和缓存失效恢复。实现参照 contract-service-be 的模型客户端与媒体协调器。

---

## 接入地址与配置

配置读取项目根目录 `.env`，进程环境变量优先，完整配置示例见 [.env.example](../../../.env.example)。MLLM 与 embedding 配置独立，不共用模型、服务地址或并发额度。

```dotenv
VLLM_MLLM_PROVIDER=vllm
VLLM_MLLM_BASE_URL=https://u953156-ae07-d6f63fdb.westd.seetacloud.com:8443/v1
VLLM_MLLM_API_KEY=
VLLM_MLLM_MODEL=qwen38-flash-next
VLLM_MLLM_ENDPOINT=chat_completions
VLLM_MLLM_TIMEOUT_SECONDS=300
VLLM_MLLM_MAX_CONCURRENT_REQUESTS=20
VLLM_MLLM_USE_MEDIA_REFERENCES=true
```

模型名来自该服务 `/v1/models` 的实际响应。请求使用 `/v1/chat/completions`；基础地址缺少 `/v1` 时客户端补齐。地址禁止嵌入用户名、密码、查询参数或片段。服务未启用鉴权时允许 API Key 留空，SDK 使用非敏感占位字符串 `vllm-local`；若启用鉴权，需在本地配置实际 Key。

生成参数沿用合同服务的命名：`ENABLE_THINKING=false`、`REASONING_EFFORT=xhigh`、`TEMPERATURE=0.7`、`TOP_P=0.8`、`TOP_K=20`、`PRESENCE_PENALTY=1.5`、`REPETITION_PENALTY=1.0`、`SEED=3407`、`MAX_COMPLETION_TOKENS=8192`，均加上 `VLLM_MLLM_` 前缀。思考开关与强度通过 `chat_template_kwargs` 发送；未开启思考时不发送强度。本适配按当前 Qwen 部署协议工作，不注入模型工具格式提示词或合同业务提示词。

`CONTEXT_WINDOW_TOKENS=262144`、`RESERVED_PROMPT_TOKENS=4096`、`RESERVED_RUNTIME_TOKENS=10240` 和 `MAX_VISUAL_TOKENS_PER_REQUEST=131072` 用于预算配置校验，均加相同前缀。它们不是实际请求的精确 token 计数；调用方仍需根据模型、文本、工具及文档总量检查上下文容量。

PDF 渲染参数同样使用 `VLLM_MLLM_` 前缀，由共享加载器读取；字段和默认值见[资源 PDF 读取与内存渲染](../document/resource-pdf.md)。每份文档的默认视觉预算是 `65536`，两份文档的估算总量可覆盖默认请求视觉预算；实际模型 token 数不由该估算保证。

---

## 客户端使用

```python
from app.config.mllm import load_mllm_settings
from app.infrastructure.mllm import MLLMClient, pdf_page_content
from app.service.resource_pdf import ResourcePDFService

settings = load_mllm_settings()
pdf = await ResourcePDFService(render_settings=settings.vision).load(
    "contract-pheno", "request_001.pdf",
)

# messages 的任务说明和业务提示词由调用方提供。
messages = [{"role": "user", "content": [
    {"type": "text", "text": task_instruction},
    *[pdf_page_content(page) for page in pdf.pages],
]}]
async with MLLMClient(settings) as client:
    response = await client.create_chat_completion(messages=messages)
    content = response.choices[0].message.content
```

`create_chat_completion` 返回 OpenAI SDK `ChatCompletion`，保留正文、工具调用、结束原因、用量和兼容服务扩展字段。支持透传 `tools`、`tool_choice`、`response_format`，可指定不超过配置上限的 `max_completion_tokens`；不解释或执行工具调用。当前只提供非流式基础调用。可显式传入布尔参数 `preserve_thinking`，作为模板的历史思考保留开关（原生响应字段参见 [vLLM 推理输出文档](https://docs.vllm.ai/en/stable/features/reasoning_outputs/)）；省略时不改变其他调用方的模板配置。

函数工具会话可传入 `tool_placement="before_task"` 或 `"after_task"` 以及 `tool_task_index`，二者必须一起提供，锚点必须是有效的 user 消息且工具列表非空。参数通过 `chat_template_kwargs` 传给服务端，判重会话固定绑定初始 B 消息；后续纠错不移动锚点。判重循环传入 `preserve_thinking=true`，历史 assistant 同时携带 `reasoning` 和 `reasoning_content` 的同一原文；当前服务的分词接口已验证该组合确实保留历史思考。服务端必须使用支持这些参数的自定义聊天模板；模拟 HTTP 已验证实际请求体，尚未验证远端工具注入位置。

客户端可显式调用 `close()`，或使用异步上下文管理器释放 HTTP 连接。默认构造读取本地配置，不发起网络请求；[文件判重子图](../../architecture/workflow/file-deduplication/readme.md)已实现对话循环，可注入共享客户端或在调用时创建并关闭客户端，应用启动也不会自动创建或调用它。

---

## 页面引用与并发

`pdf_page_content` 把 `PreparedPDFPage` 转为包含同页 `media_uuid` 和 `PNGImage` 内存引用的内容块，不提前生成 Base64。调用方不能直接把该内部内容块当成普通 JSON 请求发送。

同一事件循环中，同一服务地址和模型共享媒体协调器。其状态只有 `seeding` 和 `ready`：首次请求认领未知页面并完整发送 PNG data URL 与 UUID；重叠请求等待首次填充成功，随后用 `image_url=null` 加同一 UUID 引用。首次请求失败或取消会释放认领并唤醒等待者。

媒体等待不占用模型请求配额。单 worker 内全部 MLLM 客户端共享 `MAX_CONCURRENT_REQUESTS` 在途额度，配置不一致时明确报错；只在实际取得额度后临时编码未命中的页面。并行分支和多轮请求应共享同一份渲染对象，重新渲染会产生新 UUID。

协调器最多保留 4096 个 ready UUID 的 LRU 影子状态，不保存 PNG 或 Base64。消息替换只浅复制容器，不改写调用方上下文；一次请求中重复引用同一个 PNG 字节对象只编码一次。多进程各自维护配额和缓存状态。

该传输协议以 [vLLM 多模态输入说明](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)为准，服务端必须支持媒体 UUID 和空图片引用，并启用相应缓存。`USE_MEDIA_REFERENCES=false` 时移除 UUID，每次发送标准完整图片请求。

---

## 错误与恢复边界

当服务返回 HTTP 400 且正文同时包含 `Cache miss for` 和 `data is not provided` 时，客户端清除相应影子状态，完整重填一次。再次失败继续抛出异常，不循环重试；普通 400 不触发重填。

- `MLLMUnavailableError`：连接失败、超时、404、408、429 和 5xx；调用方决定是否重试或暂停。
- `MLLMRequestError`：其余 HTTP 错误，包括参数和鉴权失败。
- `ValueError`：本地配置、空消息、生成上限、媒体内容或并发配置不合法。

SDK 自动重试已关闭。异常说明只含类别与状态，不复制认证信息、图片、提示词或服务端错误正文。未知结果不能转换为不重复或审核拒绝。本基础设施不实现业务智能体、多轮记忆、工作区或审核规则。

---

## 验证

```bash
python -m unittest discover -s tests -p 'test_mllm.py'
```

测试覆盖配置与环境优先级、请求参数、媒体完整填充、UUID-only 引用、缓存 miss 重填、普通错误、并发等待、取消恢复、共享并发额度、关闭引用能力和 LRU 淘汰。

已使用给定服务验证：文本请求返回 `OK`；公开 PDF 的表格页面两次均识别为 `cell[0][0]`。第一次视觉请求为 `89587` bytes，第二次 UUID-only 请求为 `447` bytes，均成功且未发生重填。这是单页消息体积测量，不代表模型推理加速比；服务重启后的恢复由模拟缓存 miss 测试覆盖，未重启远端服务。
