# 通行证推荐提示词与消息

> **当前阶段：** 已实现固定系统任务提示词、三条初始消息组装、四字段 JSON 判断验收及实际 MLLM 调用。当前 PDF 已接通入库主图，所有通行证组共享同一份页面对象。

---

## 任务与事实来源

一个判断任务接收当前文件的全部页面、名称、摘要，以及一个通行证组内每个文件的平台、名称和摘要，判断当前文件是否值得关联这一组已有通行证。提示词只说明模型可观察的资料、关联原则和输出规则，不出现节点、工作流、检索或缓存等实现术语。

当前文件页面优先于名称和摘要；组内文件只有描述，不能声称读取过其原文或核对过签章。同一项目、明确事项、配套资料或直接互补关系可以支持推荐；同一行业、泛化主题、标题或模板相似本身不足以支持推荐。具体关联不足时不推荐，不能据此声称文件互不相关。

完整提示词只维护在 `app/agent/passport_recommendation/prompt.py` 的 `SYSTEM_PROMPT`，直接从文件关联任务开始，不含提示词版本或工程标识。`build_system_message()` 返回独立字典，不插入当前文件、通行证、时间或请求标识。

---

## 初始消息结构

`message.build_initial_messages(file, pdf, group)` 返回三条 chat message，全部在首次判断前提供：

```text
system
  固定系统任务提示

user
  【当前文件 -- 页面1】
  页面1图像及 UUID
  【当前文件 -- 页面2】
  页面2图像及 UUID
  ……
  【当前文件 -- 名称与摘要】
  名称、摘要

user
  本组文件描述的事实边界
  【关联文件 -- 条目1】
  平台、名称、摘要
  【关联文件 -- 条目2】
  平台、名称、摘要
  ……
```

同一当前文件的前两条消息稳定，各通行证组只改变末尾消息。新建消息容器并复用已渲染的 PNG 字节与 UUID；真实发送时由 MLLM 基础设施处理图像编码和页面引用。不重新渲染、不写临时附件、不注入通行证值、资源 ID、向量或相似度。

`pdf` 为完整 `PreparedPDF`，必须具有连续的物理页码和非空页面图像；缺页、乱序或空图像时失败，不能静默裁剪。条目序号按输入组内顺序从 1 开始，平台、名称和摘要使用 JSON 转义保持数据边界，未知名称和摘要保留 `null`。模型没有组内文件的读页工具。

---

## JSON 判断契约

`PassportGroupJudgment` 的字段顺序固定为：

```yaml
evidence:
  - current_page: 1
    group_file_index: 1
    finding: 当前文件页面事实与对应条目名称或摘要中的关联、差异或限制
reasoning: 可复核的简洁论证摘要
should_recommend: true
reason: 面向审核者、可独立阅读的简明理由
```

`evidence` 最多 32 条；页码与条目序号是严格正整数，单条说明最多 2000 字符。`reasoning` 和 `reason` 非空白，最多 10000 字符；决定为严格 boolean，不接受字符串、`null` 或额外字段。推荐必须至少提供一条证据；不推荐且无法建立可核对关联时允许空证据，但仍需清楚说明不足。

字段类型、必填项、顺序及嵌套描述只从 Pydantic 模型生成。`prompt.build_response_format()` 返回 `json_schema` 格式，实际请求携带此格式；响应仍必须通过本地校验，不能仅依赖提示词或服务端约束。

结构校验之外，使用上下文限制证据引用：

```python
result = PassportGroupJudgment.model_validate_json(
    response_content,
    context={
        "current_page_count": pdf.page_count,
        "group_file_count": len(group.files),
    },
)
```

实际调用按完整当前 PDF 的页数和该组条目数校验引用；并发节点再次验收结果。页码与条目引用有效只能证明位置存在，不能证明模型说明真实或语义判断准确。

`PassportGroupDecision` 保存四字段判断及程序绑定的通行证。结果收束继续仅返回 `{passport, reason}`，`reasoning` 不替代面向审核者的推荐理由；当前不将证据和论证新增到 SQLite 字段。

---

## 思考与接入边界

本任务单次判断后直接生成 JSON 正文，不使用完成工具或读页循环。系统提示要求充分核对资料，正文仅输出结构化结果，不要求输出完整隐式思考过程。

原生思考由 MLLM 的 `enable_thinking`、`reasoning_effort` 配置控制，提示词不能代替这些参数。调用只将正式正文作为 JSON 验收对象，原生 reasoning 字段不能充当判断结果。`audit_sink` 可逐组收集完整 SDK 响应、原生思考、用量、判断及失败信息；审计不进入模型消息或其他组的上下文。不使用模型循环、恢复记忆或工作区。

子图仍以 `file` 传递名称、摘要和向量，另通过可选 `current_pdf` 传递当前渲染对象；也可注入异步 `prepare_pdf()`。仅命中非空组且使用默认模型判断时才需要页面；无页面时只调用准备函数一次，再并发处理全部组。入库主图复用判重页面；没有判重候选时使用内存附件延迟渲染，完成后保留该份页面。

每组调用 `agent.judge_group`，完整发送当前文件与本组描述。发送前检查页面视觉预算、文本与 JSON Schema 的 UTF-8 字节保守预算及生成预留，超限明确失败，不静默截断正文或页面。请求沿用已有 MLLM 思考、采样、超时、全局请求并发和媒体缓存失效恢复配置。

返回必须有唯一响应、正常 `stop` 结束、无工具调用或 refusal，正式正文必须为完整合法 JSON。拒绝围栏包裹、重复键、非有限 JSON 数值、缺少或额外字段、非严格 boolean 和无效证据；截断、服务错误及取消不会转化为不推荐。除已有媒体缓存失效恢复外，不重试无效结果。

默认路径为整个任务创建一个共享 MLLM 客户端，待全部组完成或取消退出后关闭；显式注入的客户端由调用者关闭。组内请求互不继承历史，模型可见文本不插入通行证身份或工程标识。

提示词组织参照[本项目提示词工程规范](../../../standard/prompt-engineering.md)，整体结构见[推荐子图](readme.md)。

---

## 验证

```bash
python -m unittest tests.test_passport_agent tests.test_passport_prompt tests.test_passport_recommendation \
  tests.test_passport_search tests.test_ingestion_workflow tests.test_pending_review
```

隔离测试覆盖字段顺序和描述、正反例、推荐证据必填、越界引用、严格 boolean、完整页面顺序、PNG 与 UUID 复用、组间公共前缀、独立消息容器，以及真实客户端请求并发、思考参数、JSON 响应验收、失败取消等待、客户端释放、入库页面复用及失败不落盘/ACK。测试附件与临时文件只使用 `tests/`，不调用真实模型，不能据此认定推荐质量已经验证。

已使用 `tests/fixtures/resource-pages.pdf` 对当前配置的 vLLM 服务进行两组协议联调：两组均通过 JSON Schema 响应和本地验收，并返回原生思考。输入为合成文件描述，此结果只说明协议可用，不代表业务推荐准确率。
