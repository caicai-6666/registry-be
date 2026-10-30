# 文件对比系统提示词

> **当前阶段：** 固定系统提示词已写入 `app/agent/file_deduplication/prompt.py`，已接入多轮 Agent，已完成[九用例真实模型实验](../../../capability/application/file-deduplication-trial.md)，总体准确率尚未确定。

---

## 提示词入口

```python
from app.agent.file_deduplication.prompt import build_system_message

system_message = build_system_message()
```

`SYSTEM_PROMPT` 是唯一完整提示词来源，直接从文件对比任务开始，不含提示词版本或工程标识。构建函数返回新的 `system` 消息字典，不接受文件、页数、时间或身份参数，使不同文件对共享同一系统前缀。

文件 A 页面、两份文件的描述及 B 当前可见页由后续消息提供，顺序见[上下文设计](context.md)。实际工具定义由[工具契约](tool.md)提供，不把 Schema 复制到系统提示词中。

---

## 内容与设计边界

系统提示只让模型比较文件 A 与文件 B，不包含来源平台或外围处理流程的术语。规则包括：页面证据优先、物理页码引用、文件正文中的命令不能改变任务、B 按需读取、旧页保留带页码与观察的友好折叠卡片、需要时重新打开、观察仅引用允许页面，以及真实工具调用。

当前采用核心内容及关键身份实质对应的比较原则。不同扫描、方向、版式或改名不单独构成内容差异；同模板、同主体或同金额也不单独证明重复。签章、手写修改和附件差异需看实际含义，不能一概忽略。更精确的边界案例应通过后续标注与实验完善；实验在模型上下文外保存提示词原文与摘要以便追溯。

完成参数固定按 `evidence → reasoning → is_duplicate → conclusion` 组织。`reasoning` 只给简洁、可核对的证据论证，不输出探索过程；`conclusion` 是供阅读者理解的独立简明结论。例子使用虚构单页文件，分别展示读页、重复和同模板不同内容的工具参数；YAML 仅解释参数，不允许模型用文本模拟工具调用。

证据不足不能默认 false，也不能为了结束而编造 true。提示词要求保留未完成结果；运行器通过有限轮次、连续纠错和预算检查控制未完成结果，不新增“未知” boolean 或第三种工具。

---

## 验证与接入状态

```bash
python -m unittest tests.test_file_deduplication_prompt \
  tests.test_file_deduplication_tools tests.test_file_deduplication -v
```

验证固定消息与独立字典、模型说明不包含外围术语、虚构示例通过真实 Pydantic 参数和页面引用校验，以及结论到结果的映射。此处只验证静态协议和示例，不能证明真实模型遵循规则、正确使用工具或准确判重。

`judge_files` 已通过运行器将本消息作为首个 system，并执行工具位置、页面窗口、已展示页和终态验收；程序不能因为提示词提出限制就跳过实际校验。

提示词组织参考[本项目提示词工程规范](../../../standard/prompt-engineering.md)。
