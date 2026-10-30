# 知识沉淀注册中心后端

面向知识沉淀各子平台的文件注册与审核服务。通过 `middleware-service` 接收入库、删除申请，结合智能体辅助与人工审核管理正式文件，并将最终结果反馈给来源平台。

本项目负责注册中心后端；前端位于同级 `registry-fe`，MySQL 镜像与统一部署位于同级 [knowledge-accumulation-deploy](../knowledge-accumulation-deploy/readme.md)。

## 目录

- [主要功能](#主要功能)
- [业务流程与存储](#业务流程与存储)
- [技术栈与目录](#技术栈与目录)
- [本地开发](#本地开发)
- [容器部署](#容器部署)
- [接口与认证](#接口与认证)
- [测试与验证](#测试与验证)
- [当前运行边界](#当前运行边界)
- [进一步阅读](#进一步阅读)

---

## 主要功能

| 能力 | 说明 |
| --- | --- |
| 中间件接入 | 启动后自动登录并维持心跳，后台持续消费入库、删除申请。 |
| 入库预处理 | 下载原件、生成摘要向量、拦截同平台同源文件的重复申请。 |
| 智能判重 | 同平台 HNSW 相似文件检索，调用多模态智能体逐页比较 PDF，确认重复后自动拒绝。 |
| 通行证推荐 | 跨平台检索相关文件，按通行证聚合，并发判断关联价值并生成推荐理由。 |
| 人工入库审核 | 查看待审列表和原件、批准或拒绝、选择多个通行证、查询审核历史。 |
| 人工删除审核 | 核对正式文件与通行证、批准或拒绝、保留删除后的原件和文件信息快照。 |
| 文件与平台查询 | 获取正式文件目录、下载原件、查看子平台存活状态及下次同步时间。 |
| 人员与授权管理 | 查询员工和部门、刷新姓名/全部部门/任职资料，增量维护文件权限与平台管理员。 |
| 文件等级调整 | 管理员调整正式文件等级，保存变更日志并向来源平台发布通知。 |
| 身份与文件授权 | 管理接口校验中央登录及 registry 管理权；独立原件入口分别支持用户 token 和可信知识后端平台 token。 |

---

## 业务流程与存储

**入库：** 拉取申请 → 检查源文件身份 → 内存下载 → 摘要向量化 → 同平台相似文件判重 → 跨平台通行证推荐 → SQLite 暂存待审申请和原件 → 确认消费。确认重复时保存拒绝记录并反馈中间件。人工批准后，原件转入正式目录、资源写入 MySQL；人工拒绝不创建正式资源。最终审核均在本地处理完成后发布结果。

**删除：** 拉取申请 → 按来源平台与 `source_id` 查找正式资源 → 核对完整通行证集合 → SQLite 保存待审申请并确认消费。目标不存在或通行证不符时自动拒绝；人工批准后移除 MySQL 正式记录、保留原件和审核快照，再反馈中间件。拒绝时保留正式资源。

| 存储位置 | 用途 |
| --- | --- |
| MySQL `resources` | 仅保存审核通过的正式文件，包含摘要向量、多个通行证及已通过的申请与审核信息。 |
| SQLite `ingestion_reviews` | 入库待审申请、推荐通行证、审核决定、历史及反馈凭据。 |
| SQLite `deletion_reviews` | 删除申请、审核决定、反馈凭据，以及文件名称、摘要、原上传人快照。 |
| MySQL `departments` / `employees` / `platform_admins` | 与知识后端共享的权威人员、全部一级部门、任职资料、文件权限与管理关系。 |
| SQLite 辅助表 | 文件等级变更审计、人员资料及权限增量同步的持久重试标记。 |
| `data/review-log/registry.db` | 默认 SQLite 数据库。 |
| `data/review-log/tmp-file/ingestion/<平台>/` | 待入库审核原件。 |
| `data/resource/<平台>/` | 正式入库原件。 |
| `data/review-log/tmp-file/deletion/<平台>/` | 删除成功后的原件保留目录，暂不自动清理。 |

业务名称 `name` 与磁盘文件名 `file_url` 相互独立，后者只保存带后缀的文件名。审核记录通过资源唯一 ID 关联正式文件；删除后的历史信息直接从 SQLite 读取。详细定义见[资源表契约](description/architecture/data/resource.md)、[审核表契约](description/architecture/data/review.md)与[通行证数组契约](description/architecture/data/passport.md)。

---

## 技术栈与目录

Python 3.12、FastAPI / Uvicorn、MySQL 8.4 / aiomysql、SQLite / aiosqlite、MyVector HNSW、LlamaIndex、LangGraph、OpenAI 异步 SDK、vLLM 和 PyMuPDF。直接依赖版本固定在 [requirement.txt](requirement.txt)。

```text
registry-be/
├── app/
│   ├── main.py              # FastAPI 入口与服务生命周期
│   ├── config/              # 按功能拆分的配置
│   ├── router/              # HTTP 路由
│   ├── schema/              # HTTP 请求、响应契约
│   ├── model/               # 消息、文件与业务模型
│   ├── service/             # 后台消费、审核、文件和平台服务
│   ├── repository/          # MySQL / SQLite 数据访问
│   ├── infrastructure/      # 中间件、模型、向量和文件基础设施
│   ├── workflow/ingestion/  # 入库主工作流
│   ├── agent/               # 判重、通行证推荐子图
│   └── auth/                # 统一身份模型与认证边界
├── description/             # API、架构、能力与开发规范
├── tests/                   # 测试、固定样本和隔离临时目录
├── experiment/              # 实验、联调和检查产物
├── scripts/                 # 显式迁移与实验脚本
├── data/                    # 真实运行数据，仅提交固定目录占位
├── .env.example             # 配置示例
├── Dockerfile
└── requirement.txt
```

Git 仅追踪 `app/` 中的 Python 源码与 SQLite 初始化 SQL、部署配置与配置示例、项目描述文档，以及 `data/` 固定目录的 `.gitkeep`。`.env`、真实运行数据、`tests/`、`experiment/` 和 `scripts/` 保留在本地，不纳入版本管理；新克隆不包含这些本地测试与实验工具。完整规则见[运行配置说明](description/capability/application/runtime.md#版本管理范围)。

---

## 本地开发

以下命令从 `registry-be` 根目录开始执行。完整业务运行需要 MySQL / MyVector、中间件，以及两个提供 OpenAI 兼容接口的 vLLM 服务：embedding 和多模态模型。

### 1. 安装依赖

```bash
conda create -n registry-be python=3.12 pip
conda activate registry-be
python -m pip install -r requirement.txt
python -m pip check
```

已有环境时直接激活并安装依赖即可。

### 2. 准备配置

保留已有实际配置，首次使用时复制示例：

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

编辑 `.env`，重点核对以下配置；示例中的模型地址需要替换为实际可用地址。

| 配置组 | 主要变量与要求 |
| --- | --- |
| MySQL | `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`，与部署库一致。 |
| 中间件 | `MIDDLEWARE_BASE_URL`、`MIDDLEWARE_PLATFORM_CODE`、`MIDDLEWARE_SECRET`；平台代码和密钥须已在中间件登记。 |
| 摘要向量模型 | `VLLM_EMBEDDING_BASE_URL`、`VLLM_EMBEDDING_MODEL`、`VLLM_EMBEDDING_DIMENSIONS`；当前向量库按 4096 维配置。 |
| 多模态模型 | `VLLM_MLLM_BASE_URL`、`VLLM_MLLM_MODEL` 及对应生成、思考和上下文预算配置。 |
| 模型认证 | 两组独立的 `VLLM_*_API_KEY`，无需认证时留空。 |
| 消费开关 | `INGESTION_ENABLED`、`DELETION_ENABLED` 分别控制后台申请接收。 |
| 头像代理 | `KNOWLEDGE_ACCUMULATION_BASE_URL`，填写容器或本机可达的知识后端根地址；缓存由 `KNOWLEDGE_ACCUMULATION_AVATAR_CACHE_SECONDS` 控制。 |
| 通讯录刷新 | 按需配置 `DINGTALK_APP_KEY`、`DINGTALK_APP_SECRET`，仅用于已有员工姓名、部门及任职资料刷新。 |
| 用户认证 | 用户在知识后端登录；registry 使用 `AUTH_REQUEST_TIMEOUT_SECONDS` 配置中央 token 查询超时。 |

用户登录模式和钉钉登录凭据在 `knowledge-accumulation-be` 配置，registry 不再加载开发固定 token 或钉钉登录配置。两后端需读取同一权威人员与权限数据源；注册中心保留人员权限增量更新和平台管理员维护。

环境变量优先于 `.env`。完整认证参数见[人员系统](description/capability/application/personnel.md)，模型与运行参数见[运行配置](description/capability/application/runtime.md)、[向量服务](description/capability/infrastructure/myvector.md)和[多模态服务](description/capability/infrastructure/mllm.md)。

### 3. 启动开发数据库与中间件

数据库由同级部署项目管理：

```bash
cd ../knowledge-accumulation-deploy
test -f .env || cp .env.example .env
```

填写部署 `.env` 中的 MySQL root 密码、业务账号密码和数据库信息，确保后端 `.env` 使用相同的业务连接信息，然后执行：

```bash
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.dev.yml up -d --build --wait mysql
cd ../registry-be
```

本机 IDE 开发采用独立的 `docker-compose.dev.yml`；将后端 `MYSQL_PORT` 对齐部署 `.env` 的 `MYSQL_PORT`（当前本机开发约定为 `13306`）。统一管理方式见[开发基础设施说明](../dev-infra/README.md)。

新空卷由容器初始化正式资源、向量缓存、部门、员工（含任职资料与多部门字段）、子平台管理员表和 MyVector 插件，后端不负责创建 MySQL 表。MySQL 仅保留最终版本初始化定义，已有卷不会自动更新表结构，具体见[版本边界](description/architecture/data/resource.md#初始化与版本边界)；SQLite 在应用启动时自动升级。

中间件由独立项目部署，默认本机地址为 `http://127.0.0.1:22000`。启动后端前确认中间件及模型服务已准备好。

### 4. 启动后端

```bash
python app/main.py
```

也可直接在 IDE 中运行该文件。默认监听 `127.0.0.1:16000`，开启 Python 代码热重载：

- Swagger：<http://127.0.0.1:16000/docs>
- OpenAPI：<http://127.0.0.1:16000/openapi.json>
- 业务接口前缀：`/dev/knowledge-accumulation/registry/api`

`.env` 修改后需要手动重启。代码重载后会重新登录中间件，可能需要等待旧会话过期；用户 token 由中间件维护，不因 registry 重启自行失效；中央会话过期或用户重新登录后，旧 token 才会失效。

直接启动入口设置 `APP_PATH_PREFIX=/dev`，重载子进程继承此设置；头像代理调用知识后端也携带 `/dev`。ASGI 导入入口默认使用生产路径；统一 Compose 显式将该变量设为空。开发知识后端根地址填写 `http://127.0.0.1:15000`。

---

## 容器部署

统一部署使用 [knowledge-accumulation-deploy/docker-compose.yaml](../knowledge-accumulation-deploy/docker-compose.yaml)，包含 MySQL、registry 后端与前端、知识后端与统一前端。先按[统一部署说明](../knowledge-accumulation-deploy/readme.md)准备两个后端配置、部署 `.env` 及共享人员/知识业务表，再从本项目根目录执行：

```bash
cd ../knowledge-accumulation-deploy
docker compose config --quiet
docker compose up -d --build --wait backend registry-frontend
docker compose ps
```

部署 `.env` 中的 `REGISTRY_BACKEND_ENV_FILE` 默认指向 `../registry-be/.env`，按 raw 模式注入。Compose 覆盖数据库连接、持久化目录和空的 `APP_PATH_PREFIX`；头像代理默认直接访问 `http://knowledge-backend:8000`。知识后端与统一入口的准备、迁移和启动按部署文档独立完成，不能用 HTTP 健康状态代替业务表与依赖检查。

| registry 入口 | 默认地址 |
| --- | --- |
| 后端 API | `http://127.0.0.1:23000/knowledge-accumulation/registry/api` |
| Swagger | `http://127.0.0.1:23000/docs` |
| 前端 | `http://127.0.0.1:23080/knowledge-accumulation/registry/` |

后端容器工作目录为 `/workspace`，内部端口 `8000`。`BACKEND_PORT`、`REGISTRY_FRONTEND_PORT` 可修改宿主机端口，默认只绑定 `127.0.0.1`。外层 HTTPS 网关须将 registry 子路径优先转发到其前端，并让知识统一入口与 registry 页面共用同一源，以读取统一入口保存的用户 token。

Compose 使用命名卷持久化 MySQL 和 registry 运行数据。本机文件不会自动进入容器卷；旧库不会因重建镜像自动补列，具体见[人员数据初始化边界](description/architecture/data/employee.md#初始化与接入边界)。同一平台身份只运行一个后端实例。

---

## 接口与认证

用户先在知识后端登录取得用户 `access_token`。registry 每次直接向中间件查询该 token，取得可信 userid 并续期；不要求其他平台访问知识后端 verify。用户接口使用以下请求头，最终审核人使用服务端确认的 userid：

```text
Authorization: Bearer <access_token>
```

| 接口领域 | 文档 |
| --- | --- |
| 当前登录身份 | [当前身份 API](description/api/auth.md) |
| 员工、部门、资料刷新与授权维护 | [人员管理 API](description/api/user.md) |
| 平台列表、下次同步时间 | [平台查看 API](description/api/platform.md) |
| 正式文件基本信息与通行证 | [文件目录 API](description/api/catalog.md) |
| 正式原件、待入库原件 | [文件读取 API](description/api/resource.md) |
| 待入库列表、审核历史、最终决定 | [入库审核 API](description/api/ingestion-review.md) |
| 待删除列表、审核历史、最终决定 | [删除审核 API](description/api/deletion-review.md) |

同一用户重新登录后，中央旧 token 立即失效；registry 不维护另一份本地用户 token 映射或固定 TTL，重启不会自行撤销中央用户会话。未知或过期 token 返回 401，中央用户或管理员查询故障返回 503；`GET /auth/me` 只返回中央 `userid`、`username` 并验证注册中心管理权限，旧 registry 登录接口已移除。详情见[中央用户认证](description/capability/infrastructure/middleware-user.md)。

registry 管理接口均要求有效中央用户 token 与注册中心（默认 `registry`）管理员身份，包括当前身份、平台列表和管理员原件入口。普通用户通过独立的[文件权限下载入口](description/api/resource.md#按用户权限获取正式文件原件)，按来源平台的当前员工等级访问正式原件；文件等级不赋予管理权。头像由知识后端存储；registry 保留管理员专用代理 `/knowledge-accumulation/registry/api/resource/avatar/{filename}`，转发同一次请求的用户 token，配置见[头像代理](description/capability/infrastructure/avatar.md)。

知识后端助理机器人与定时任务可调用[可信后台原件接口](description/api/resource.md#可信后台代用户获取正式文件原件)：使用 knowledge-accumulation 平台 Bearer 和可信任务绑定的 userid，按员工当前 SQL 文件权限读取，不要求用户登录。需先部署中间件只读平台令牌校验接口；依赖故障时拒绝下载。

完整路径、请求响应和错误语义以 [API 参考](description/api/readme.md)及 OpenAPI 为准。中间件的平台令牌与生产登录 token相互独立。

---

## 测试与验证

以下命令仅适用于本地另行保留了 `tests/` 的开发环境；测试目录不纳入 Git，新克隆及部署镜像不包含测试。准备好本地测试工具后，在已激活的 Python 环境、项目根目录下执行：

```bash
python -m unittest discover -s tests -v
```

默认测试使用模拟中间件与模型；真实 MySQL、MyVector 集成测试需要独立临时数据库，未提供端口时会跳过。准备独立插件容器及 `registry_test` 数据库后，通过 `REGISTRY_TEST_MYSQL_PORT`、`REGISTRY_TEST_MYVECTOR_PORT` 指定其端口运行；测试账号约定及初始化方式见本地 `tests/test_resource_mysql.py` 和 `tests/test_passport_hnsw.py`。集成测试会创建、修改和删除测试表，不能指向开发或正式业务库。

测试临时数据放在 `tests/tmp/`，结束后清理；固定样本放在 `tests/fixtures/`，实验和联调产物放在 `experiment/`。`data/` 仅用于真实运行数据。规则见[测试与实验产物规范](description/standard/experiment.md)；历史实验入口 `experiment/readme.md` 仅在另备本地产物的环境中可用。

---

## 当前运行边界

- 当前按单实例、单 worker 运行；平台会话、消息处理锁和部分运行状态保存在进程内；用户 token 以中间件中央会话为准。
- 智能判重与通行证推荐当前以 PDF 逐页渲染为基础；其他类型原件的下载支持不等同于智能审核支持。
- HNSW 是近似检索，候选数量受 Top-K 限制；相似度阈值和智能体效果仍需结合真实业务样本持续评估。
- 本地存储先完成，再向中间件发布审核结果；发布成功不代表来源平台已完成消费。跨系统没有统一事务，未确认反馈会保留恢复状态。
- 平台目录尚未同步或已过期时返回 `503`；容器健康检查只验证 Web 入口，不代表数据库、中间件和模型全部可用。

---

## 进一步阅读

- [项目总览](description/project.md)：职责与系统边界。
- [项目文档导航](description/readme.md)：全部专题入口。
- [入库主工作流](description/architecture/workflow/ingestion/readme.md)：前置处理、判重、推荐与持久化。
- [文件判重子图](description/architecture/workflow/file-deduplication/readme.md)与[通行证推荐子图](description/architecture/workflow/passport-recommendation/readme.md)：智能体契约与实现。
- [开发规范](AGENTS.md)与[文档风格](description/documentation.md)：参与开发前的约定。
