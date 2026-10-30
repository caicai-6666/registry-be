# 项目文档导航

本文提供知识沉淀注册中心后端的统一文档入口，按开发、接口接入、数据契约和运行能力查找当前实现。

---

## 从哪里开始

- [项目首页](../README.md)：快速了解功能、开发启动、容器部署和测试入口。
- [项目总览](project.md)：了解项目定位、基本职责、核心术语和总体业务流程。
- [文档撰写风格手册](documentation.md)：了解文档归档、命名、排版与维护要求。
- [版本管理范围](capability/application/runtime.md#版本管理范围)：了解部署文件、描述文档、目录占位与本地产物的交付边界。
- [提示词工程规范](standard/prompt-engineering.md)：任务边界、模型可见信息、实际工具 Schema 字段描述和验证要求。
- [多轮 Agent 上下文与记忆管理规范](standard/agent-context-management.md)：连续纠错清理、权威状态、合法历史和私有审计隔离。
- [测试与实验产物规范](standard/experiment.md)：了解测试、实验和真实运行数据的目录边界。
- [统一部署说明](../../knowledge-accumulation-deploy/readme.md)：registry 前后端、知识后端与共享 MySQL 的配置和启动。

---

## 基础设施

- [员工头像代理与存储边界](capability/infrastructure/avatar.md)：了解头像转发、目标地址及端口配置、目录迁移与职责边界。
- [审核日志与文件存储](capability/infrastructure/review-file-storage.md)：了解 SQLite 位置、入库暂存、正式文件晋升、删除保留和旧目录迁移。
- [SQLite 审核数据基础设施](capability/infrastructure/sqlite.md)：了解运行数据位置、异步事务、初始化与生命周期。
- [vLLM 多模态模型接入](capability/infrastructure/mllm.md)：了解模型配置、异步调用、PDF 页面 UUID 复用与错误恢复。
- [MyVector 与 vLLM 向量检索试用](capability/infrastructure/myvector.md)：了解 LlamaIndex 模型接入、异步匹配和隔离验证。
- [MySQL 与统一部署](capability/infrastructure/mysql.md)：了解数据库镜像、统一部署项目及当前实现边界。
- [中央用户认证](capability/infrastructure/middleware-user.md)：了解直接 token 查询、续期及用户注册与维护职责边界。
- [中间件注册与心跳](capability/infrastructure/middleware-session.md)：了解启动登录、会话续期、故障恢复及配置。

---

## 应用能力

- [用户文件权限与平台管理员变更](capability/application/user-access.md)：增量更新文件等级和管理关系，先提交本地再即时同步中间件。
- [已有员工资料手动刷新](capability/application/personnel-refresh.md)：配置钉钉应用，刷新姓名、部门及任职资料，并持久重试姓名和全部部门名称的增量同步。
- [登录与平台管理依赖](capability/application/authorization.md)：了解管理接口、用户原件和可信后台原件三类授权；管理员总表由知识后端发布。
- [人工删除审核服务](capability/application/deletion-review.md)：了解最终删除、原件保留、拒绝与反馈恢复。
- [人工入库审核服务](capability/application/ingestion-review.md)：了解最终决定、文件晋升、正式入库及反馈恢复。
- [相似文件召回鲁棒性实验](capability/application/recall-robustness.md)：了解真实 PDF、摘要扰动、阈值与候选数量评估方法和适用边界。
- [平台目录刷新服务](capability/application/platform-directory.md)：了解启动查询、周期更新、内存快照及存活状态的时效边界。
- [向量化指令探索实验](capability/application/embedding-instruction-trial.md)：了解原文、任务指令、输入格式及分组验证方法。
- [正式文件等级调整](capability/application/file-level-adjustment.md)：了解等级修改、中间件发布及 SQLite 独立审计日志。
- [人员身份与职责](capability/application/personnel.md)：了解知识后端登录与 registry 权限、管理员维护边界。
- [入库源文件身份拦截](capability/application/source-identity.md)：了解同平台、同源 ID 的待审核及正式资源阻断、消费重投与并发约束。
- [待审核文件持久化](capability/application/pending-review.md)：了解原附件与 SQLite 完整待审核申请的保存顺序和重投保护。
- [Python 运行环境与依赖](capability/application/dependencies.md)：了解基础依赖、开发环境及安装方式。
- [环境配置与容器运行](capability/application/runtime.md)：了解环境变量、启动入口、镜像构建和文件忽略规则。
- [删除申请接收服务](capability/application/deletion-consumer.md)：了解无附件删除申请、正式文件引用、自动拒绝及 ACK 恢复。
- [入库申请接收服务](capability/application/ingestion-consumer.md)：了解完整入库图的后台接入、SQLite 待审准备、失败重试及 ACK 恢复。

---

## 文档处理

- [资源 PDF 读取与内存渲染](capability/document/resource-pdf.md)：按平台 code 和附件文件名读取 PDF，获取带页面 UUID 的内存 PNG 对象。

---

## 工作流

- [判重 Agent 上下文设计](architecture/workflow/file-deduplication/context.md)：了解待评估全文前缀、召回页窗口、工具注入、折叠和完成工具的设计契约。
- [通行证推荐子图](architecture/workflow/passport-recommendation/readme.md)：了解跨平台 HNSW 搜索、通行证聚合、组级并发判断与结果收束。
- [通行证推荐提示词与消息](architecture/workflow/passport-recommendation/prompt.md)：了解当前全文、组内文件描述及证据、论证、推荐决定和理由的 JSON 契约。
- [文件判重子图](architecture/workflow/file-deduplication/readme.md)：了解节点 C 使用的多轮文件对比、友好页面折叠、严格结论与未知失败边界。
- [文件判重实际模型实验](capability/application/file-deduplication-trial.md)：运行真实模型的小样本鲁棒性检查，查看协议、折叠、思考与结论证据。
- [入库申请工作流](architecture/workflow/ingestion/readme.md)：了解前置处理、并行去重、拒绝反馈、通行证推荐和 ACK 边界。

---

## 数据契约

- [公司部门与员工契约](architecture/data/employee.md)：了解全部一级部门、任职资料、平台权限 JSON 映射、管理员关联及新旧库边界。
- [员工刷新与资料待同步契约](architecture/data/personnel-refresh.md)：姓名、部门和任职资料更新，SQLite 待同步标记及部分失败结果。
- [用户授权变更契约](architecture/data/user-access.md)：增量文件等级、管理员关系和持久重试标记。
- [正式文件等级变更契约](architecture/data/file-level-adjustment.md)：等级变更审计、发布状态和历史字段。
- [文件通行证数组契约](architecture/data/passport.md)：了解多通行证、跨组推荐、完整删除校验及已有数据库升级。
- [平台目录内存契约](architecture/data/platform-directory.md)：了解公开平台字段、不可变快照与过期标记。
- [登录身份契约](architecture/data/authenticated-user.md)：了解当前身份的最小字段、内部员工投影及三类文件授权边界。
- [注册中心审核表契约](architecture/data/review.md)：了解完整入库暂存、轻量删除申请各自的字段、状态及资源关联约束。
- [注册中心资源表契约](architecture/data/resource.md)：了解正式文件字段、入库备注、文件名、向量和通行证约束。

---

## 对外接口

- [API 参考](api/readme.md)：查看完整接口目录与接入流程。
- [当前身份 API](api/auth.md)：使用中央用户 token 查询当前身份。
- [人员管理 API](api/user.md)：查询员工和部门、手动刷新资料、增量维护文件权限及管理员关系。
- [平台查看 API](api/platform.md)：查看包含注册中心自身的全部平台及存活状态、下次计划同步时间。
- [正式文件目录 API](api/catalog.md)：获取所有正式文件的 ID、来源平台、入库文件名称、入库申请人、审核人、入库时间、摘要和最终通行证。
- [正式与待审文件原件 API](api/resource.md)：管理员原件与头像代理、用户文件等级下载、可信知识后端后台下载及各自认证方式。
- [文件删除审核 API](api/deletion-review.md)：获取待删除申请、按审核员查询历史并确定是否删除。
- [文件入库审核 API](api/ingestion-review.md)：获取待审申请与推荐通行证，确定同意或拒绝入库，并按审核员查询历史。
