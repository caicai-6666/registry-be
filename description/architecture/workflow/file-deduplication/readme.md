# 文件判重子图

> **当前阶段：** 已实现 LangGraph 子图、模型对话循环及节点 C 的调用适配。文件 A 全文常驻，文件 B 按需读页并友好折叠；只有经过校验的完成工具能返回结论。执行机制已隔离测试，并完成[九用例真实模型实验](../../../capability/application/file-deduplication-trial.md)，总体准确率仍需扩大样本评估。

---

## 职责与文件结构

子图位于 `app/agent/file_deduplication/`，负责判断一对文件是否重复，并给出理由。它不拉取申请、不查询候选、不读取磁盘、不发布拒绝结果，也不 ACK；这些职责由[入库主工作流](../ingestion/readme.md)承担。

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | 导出构建器与输入输出模型。 |
| `state.py` | 定义文件对、严格判重结果和子图状态。 |
| `context.py` | 注入模型客户端、独立配置及每次会话的私有审计接收器。 |
| `node.py` | 输入校验、实际对话运行、客户端生命周期和输出校验。 |
| `workflow.py` | 组装独立子图，固定校验节点，只允许替换判断节点。 |
| `tool.py` | 读页与完成工具的 Pydantic 参数、Schema、页面引用校验和结果投影。 |
| `prompt.py` | 文件 A/B 固定系统任务提示词及 system 消息构建，不注入工程元信息。 |
| `message.py` | 固定前缀、读页工具的多模态内容及工具历史中的折叠卡片。 |

运行器 `agent.py` 接入[判重工具契约](tool.md)与[固定系统提示词](prompt.md)，每轮只接受一个真实工具调用；无独立工作区对象或状态消息；循环局部页码及计数用于工具校验，原始响应完整保存在私有审计，通过校验动作的原生思考及工具历史持续进入后续上下文；旧图仍按窗口友好折叠。

文件 A 的[页面消息组装](context.md#文件-a-页面组装)已准备，复用现有完整 `PreparedPDF`，不重新读取或渲染 PDF。

实际 Agent 的首轮顺序、vLLM 工具锚点、召回文件视觉窗口、页面折叠及多轮边界已记录在[上下文与页面窗口设计](context.md)。其中记录实际会话生命周期、独立配置和已知限制。

---

## 拓扑

```mermaid
flowchart LR
    S([开始]) --> I[校验文件对]
    I --> J[智能体多轮判断]
    J --> V[校验 boolean 与理由]
    V --> E([结束])
```

图不在当前文档阅读环境渲染时，可按“开始 → 校验文件对 → 智能体判断 → 校验结果 → 结束”阅读。

---

## 输入输出契约

输入状态仅包含 `pair: FileDeduplicationInput`：

```yaml
pair:
  current_file:
    name: 当前申请的文件展示名称，未知时为 null
    abstract: 当前申请摘要，未知时为 null
    pdf: PreparedPDF，包含全部页面的 PNG 与媒体 UUID
  similar_file:
    name: 一份相似候选的文件展示名称，未知时为 null
    abstract: 相似候选摘要，未知时为 null
    pdf: PreparedPDF，已由主图读取并渲染
    applicant: 申请人名称，未知为 null
    reviewer: 审核人名称，未知为 null
```

`pdf` 的来源平台与附件文件名表示原文件身份；数据结构见[资源 PDF 服务](../../../capability/document/resource-pdf.md)。文件不以 Base64、临时文件或外部 URL 进入子图。PDF 必须有页面，数量与 `page_count` 一致、页码从 1 连续排列、页面字节非空。输入不把缺失的名称或摘要补成虚构信息。

输出状态仅包含 `result: FileDeduplicationResult`：

```yaml
result:
  is_duplicate: 严格 boolean，true 或 false
  reason: 文件比较的非空最终结论，由完成工具的 conclusion 映射，最多 10000 字符
```

字符串 `"false"`、数字 `0/1`、`null`、缺少理由或空白理由均不合法。重复和不重复都必须给出理由，输出模型禁止额外字段。输入模型及 PNG 页面不随子图结果返回主图。

---

## 调用与扩展

```python
from app.agent.file_deduplication import build_file_deduplication_workflow
from app.agent.file_deduplication.context import FileDeduplicationContext

workflow = build_file_deduplication_workflow()
output = await workflow.ainvoke(
    {"pair": pair}, context=FileDeduplicationContext(mllm=mllm_client),
)
```

注入模型客户端时，由创建者负责关闭；未注入时，判断节点按本地配置创建客户端，并在成功、失败或取消后关闭。建议并行调用共享一个由调用方管理的客户端。模型接口配置和页面 UUID 传输见[MLLM 基础设施](../../../capability/infrastructure/mllm.md)。

`FileDeduplicationContext.settings` 可注入 `FileDeduplicationSettings`，否则加载独立的 `FILE_DEDUPLICATION_*` 配置。`audit_sink` 接收本次会话的审计列表，包含每轮原始响应、用量、耗时及接受或拒绝记录；不混入输出状态，不默认写文件。每个并发分支生成自己的审计列表，接收器应能安全处理多个独立会话。

测试或后续实现可通过 `build_file_deduplication_workflow(judgment_node=handler)` 指定判断节点。handler 接收子图状态，返回 `{"result": ...}`；两侧契约校验不能被该参数替换。节点 C 通过状态适配后调用编译子图，再把通过校验的 `is_duplicate` 和 `reason` 写入对应候选的 `ComparisonResult`。

子图采用[LangGraph 的节点内调用子图模式](https://docs.langchain.com/oss/python/langgraph/use-subgraphs#call-a-subgraph-inside-a-node)，因其输入输出与主图不同，主图只保留结果，不合并子图私有状态。

---

## 失败与审核边界

模型不可用、上下文或执行预算不足、连续纠错超限、无效终态及候选文件读取失败，由 C 记录为未知结果与错误类型，不伪造 false、不保存异常原文或失败模型对话。

主图在同批候选结果收齐后执行汇总：有明确 true 可以拒绝；没有 true 且存在未知结果则中断，不持久化待审核记录或 ACK。智能体理由保存在比较结果和 `duplicate_reason` 中，拒绝分支将它与命中文件的 `source_id` 组织成 Markdown 审核意见，审核员固定为“注册中心自动审核员”。发布格式与长度边界见[主图拒绝结果契约](../ingestion/readme.md#拒绝结果契约)。

---

## 验证

```bash
python -m unittest tests.test_file_deduplication_agent tests.test_file_deduplication
python -m unittest discover -s tests -p 'test_ingestion_workflow.py'
```

使用确定性模型响应与模拟 HTTP 验证实际循环、固定工具锚点、人员信息、友好折叠、重开 UUID、错误恢复及私有审计、有限预算和并发隔离；主图测试继续验证当前 PDF 仅准备一次、五候选并发、结论保留以及失败不 ACK。单元测试不调用真实模型；实际模型的小样本结果见[判重实验说明](../../../capability/application/file-deduplication-trial.md)，不能据此推算总体准确率。
