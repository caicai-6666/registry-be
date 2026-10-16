# 文件判重实际模型实验

> **用途：** 使用真实 vLLM 服务调用判重 LangGraph 子图，验证页面证据判断、多轮工具及友好折叠。该实验绕过业务主图，不读取业务数据库，不发布审核反馈。

---

## 运行方式

入口为 `scripts/file_deduplication_trial.py`，使用项目 Python 环境及本地模型、渲染、判重配置。图像变体额外使用实验环境中已有的 Pillow；不属于后端生产依赖。

```bash
python scripts/file_deduplication_trial.py --concurrency 2
python scripts/file_deduplication_trial.py --cases exact-single --concurrency 1
```

默认参考 `experiment/recall-robustness/20261013-full` 中的 inventory、manifest、摘要和人工难例标注，源文件路径由参考 manifest 提供。可使用 `--reference` 指定具有相同结构的参考目录；每次创建唯一运行目录，禁止覆盖旧产物。

`--timeout` 为单用例总超时，默认 240 秒；`--concurrency` 为实验用例并发，默认 2，与模型请求配额和业务比较并发分别管理。源文件与参考 SHA-256 不一致时中止。

---

## 用例与标签

| 类别 | 用例 | 预期 |
| --- | --- | --- |
| 相同内容 | 相同单页、改名及错误摘要、旋转 180 度、灰度 JPEG 质量 55 | 重复。 |
| 难负例 | 同模板同金额但编号及规格数量不同；日期及切割要求不同 | 不重复。 |
| 元数据干扰 | 不同合同故意给相同名称与摘要 | 不重复。 |
| 多页正例 | 相同五页合同，含正文、签章及设计图纸 | 重复。 |
| 末页反例 | 前四页相同，第五页替换为另一交易合同页 | 不重复，受控合成反例。 |

图像变体只修改实验内存 PNG，不改原 PDF。难负例依据先前人工标注及本次视觉检查；不同文件名或 SHA-256 本身不是负例标签依据。

---

## 产物与分析

产物位于 `experiment/file-deduplication/<运行标识>/`：

- `manifest.json`：源文件、模型、生成、渲染、Agent 配置、提示词指纹及预期标签；不保存密钥。
- `system-prompt.txt`：固定系统任务文本。
- `<用例>/requests.json`：发送前完整消息与工具参数，图像仅保存 UUID 和指纹。
- `<用例>/audit.json`：原始模型响应、思考、用量、工具接受状态及反馈。
- `<用例>/result.json`、`results.json`、`metrics.json`：结论、执行用时、轮次及小样本结果。
- `preview/` 和变体 PNG：页面视觉核对。
- `source-integrity.json`：运行结束后的原文件完整性校验。

`opened_b_pages` 记录成功打开的页面，不能单独证明已经展示。分析阶段通过 `requests.json` 和成功响应核对实际展示页，可另存 `validation.json`；分析和运行器快照由分析工作另行保存，运行器不自动编写 `analysis.md`。

分析记录遵循[实验规范](../../standard/experiment.md)，不修改既有原始响应或指标。

---

## 首次实测与限制

2026-10-13 使用配置的 `qwen38-flash-next`、开启思考及 `xhigh` 参数，九个用例全部完成并符合预期，无协议拒绝。五页正反例均实际展示全部 B 页面，反例还重开前两页；单用例 6.280—18.358 秒，中位数 7.956 秒。详见[九用例分析](../../../experiment/file-deduplication/20261013-154204-651110/analysis.md)与[单用例试跑](../../../experiment/file-deduplication/20261013-154123-020741/analysis.md)。

这是一组有意选择的小样本，不代表总体准确率；不可读页、真实重扫、修订和长附件仍需进一步标注验证。非零思考 token 证明实际开启思考，不能单独证明服务端如何解释 `xhigh`。客户端工具锚点已传入，但服务端工具块的实际 token 位置仍未独立检查。

---

## 原生思考持续保留验证

2026-10-13 修改循环以持续保留全部已接受动作的原生思考，重新运行九个用例，boolean 均符合预期。逐轮检查后续 assistant 中的完整思考与先前 SDK 响应一致，且 B 图像窗口仍不超过 3 页。服务端 `/tokenize` 对照证明：当前接口仅传 `reasoning_content` 会丢失原文；同时传 `reasoning` 与 `reasoning_content`，并开启 `preserve_thinking=true`，已验证原文实际进入 token 序列。

结果及兼容性发现见[原生思考保留分析](../../../experiment/file-deduplication/20261013-155524-119987/analysis.md)。布尔结论正确仍不代表反馈细节全部正确；本次同模板反例有日期误读，已在该报告单独记录。
