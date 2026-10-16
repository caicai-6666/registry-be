# 判重 Agent 工具契约

> **当前阶段：** 已准备两个工具的 Pydantic 参数模型、模型可见 Schema 和页面引用校验，位于 `app/agent/file_deduplication/tool.py`。已由 `agent.py` 接入模型、多轮运行器及真实内存页面窗口；本模块继续保持纯参数校验。

A 和 B 是需要比较的两个文件。上下文与折叠设计见[上下文说明](context.md)，对外结果见[子图入口](readme.md)。

---

## 打开指定页码

工具方法为 `read_candidate_pages`，只针对程序绑定的 B，不允许模型传文件路径或其他文件 ID。

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `page_numbers` | 严格整数列表，必填 | 非空、1-based、互不重复、在 B 页数内；数量不超过 n，保持指定顺序。 |
| `observations` | 页面观察列表，可省略 | 默认空，最多 32 项，供切换窗口前保留已有观察。 |

页面观察结构：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `current_page` | 严格正整数 | A 的有效物理页码。 |
| `candidate_page` | 严格正整数 | B 的有效物理页码，读页时只能引用当前已展示的窗口。 |
| `finding` | 字符串 | 非空、不能全空白，最多 1000 字符；简述页面对应事实或关键差异。 |

```json
{
  "page_numbers": [1, 3],
  "observations": []
}
```

方法接收真实 A/B 页数、n 和 `visible_candidate_pages`，返回通过 Pydantic 与引用校验的参数。拒绝空列表、重复页、布尔值、字符串/浮点页码、越界、超量页和额外字段。

当前方法只完成参数验收；执行器在全部校验及预算检查通过后接受工具历史与局部页码信息，失败保留原历史。页面通过对应 tool 回执的多模态 content 返回，不新增 user 或独立状态消息；只有实际送入模型并成功收到响应后才计入 `presented_pages`。

---

## 提交最终判断结果

工具方法为 `finish_comparison`。

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `evidence` | 页面观察列表 | 必填，1 至 32 项；B 引用必须属于已实际展示的页面。 |
| `reasoning` | 字符串 | 必填，非空且不能全空白，最多 10000 字符；基于页面证据的简洁论证。 |
| `is_duplicate` | 严格 boolean | 必填，只接受 true/false，不接受字符串、数字或 null。 |
| `conclusion` | 字符串 | 必填，非空且不能全空白，最多 2000 字符；供阅读者理解的简明最终结论。 |

```json
{
  "evidence": [
    {
      "current_page": 1,
      "candidate_page": 1,
      "finding": "A 的编号和标的规格与 B 不同。"
    }
  ],
  "reasoning": "文件编号不同，标的规格与数量也不同，因此不能仅因模板一致就判断重复。",
  "is_duplicate": false,
  "conclusion": "两个文件对应不同编号和不同规格的交易，不重复。"
}
```

方法接收真实 A/B 页数及 `presented_candidate_pages`，返回合法参数；B 可以已折叠，但必须实际展示过。只有加载而未展示的页面不属于有效证据。

此处检查结构与页面引用，不证明模型事实正确。后续运行器仍需验收证据充分性及终态唯一性；证据不足不能伪造 false。

参数按 `evidence → reasoning → is_duplicate → conclusion` 声明，Schema 与 Pydantic 序列化均保持该顺序。`reasoning` 是内部简洁论证，`conclusion` 是最终简明结论；移除原 `coverage`，不再要求提交检查范围说明。

通过完整验收后，`comparison_result()` 将 `conclusion` 映射为现有 `FileDeduplicationResult.reason`；`evidence` 和 `reasoning` 保留在私有审计。结果确认重复时，现有拒绝流程把这份最终结论与文件 `source_id` 拼接为 Markdown 审核意见，不把内部论证直接拼入反馈。工具说明只讲文件比较，反馈格式由外围服务处理。

---

## Pydantic 与工具说明

工具模型的 docstring、字段描述及后续提示词均只描述文件 A 与文件 B 的比较任务，不向模型引入文件来源或外部处理流程的术语。

```python
from app.agent.file_deduplication.tool import build_file_deduplication_tools

tools = build_file_deduplication_tools(max_visible_pages=3)
```

工具名称自动取执行方法名，**工具使用说明定义在对应 Pydantic 参数模型的 docstring 首行**，并自动生成模型可见的工具 `description`。执行函数的注释只解释内部校验与处理，不作为模型指令。参数及嵌套字段通过 `Field(description=...)` 描述，不另外维护手工参数 Schema。

读页模型用 `create_model()` 继承共同参数模型并添加 n 的 `max_length`；同一模型用于运行校验与 `model_json_schema()`，因此数量上限与模型可见 `maxItems` 一致。严格页码和最小值来自元素的 Pydantic `Field`，重复检查由继承的校验器完成，Schema 的 `uniqueItems` 在字段声明中提供。

B 的具体页数在任务元数据中，由执行前引用校验检查，不注入工具 Schema；同一 n 下不同文件对拥有相同的工具定义。工具采用 `strict=false`，运行器使用 `auto` 并逐轮只接受一个允许的工具调用。

---

## 验证与边界

```bash
python -m unittest tests.test_file_deduplication_tools tests.test_file_deduplication -v
```

覆盖页码类型与范围、重复和超量页、路径注入、观察来源、折叠页证据、未展示页拒绝、严格 boolean、空理由、结果投影、嵌套字段描述、Pydantic 模型首行工具说明及 Schema 稳定性；执行函数注释变化不能影响模型可见说明。

当前未修改 `judge_files`，没有真实模型调用或文件读取；本轮测试不能证明多轮 Agent 已实现。工具执行、状态提交、窗口折叠和最终证据验收将在运行器中接入。
