# 知识沉淀注册中心后端

面向知识沉淀各子平台的文件注册与审核服务。通过 `middleware-service` 接收入库、删除申请，结合智能体辅助与人工审核管理正式文件，并将最终结果反馈给来源平台。

本项目负责注册中心后端；前端位于同级 `registry-fe`，MySQL 镜像与统一部署位于同级 [registry-deploy](../registry-deploy/readme.md)。

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
| 人员认证 | 使用独立 YAML 名单和人员密钥登录，由服务端确定最终审核人。 |

---

## 业务流程与存储

**入库：** 拉取申请 → 检查源文件身份 → 内存下载 → 摘要向量化 → 同平台相似文件判重 → 跨平台通行证推荐 → SQLite 暂存待审申请和原件 → 确认消费。确认重复时保存拒绝记录并反馈中间件。人工批准后，原件转入正式目录、资源写入 MySQL；人工拒绝不创建正式资源。最终审核均在本地处理完成后发布结果。

**删除：** 拉取申请 → 按来源平台与 `source_id` 查找正式资源 → 核对完整通行证集合 → SQLite 保存待审申请并确认消费。目标不存在或通行证不符时自动拒绝；人工批准后移除 MySQL 正式记录、保留原件和审核快照，再反馈中间件。拒绝时保留正式资源。

| 存储位置 | 用途 |
| --- | --- |
| MySQL `resources` | 仅保存审核通过的正式文件，包含摘要向量、多个通行证及已通过的申请与审核信息。 |
| SQLite `ingestion_reviews` | 入库待审申请、推荐通行证、审核决定、历史及反馈凭据。 |
| SQLite `deletion_reviews` | 删除申请、审核决定、反馈凭据，以及文件名称、摘要、原上传人快照。 |
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
│   └── user/                # 审核人员定义与名单加载
├── description/             # API、架构、能力与开发规范
├── tests/                   # 测试、固定样本和隔离临时目录
├── experiment/              # 实验、联调和检查产物
├── scripts/                 # 显式迁移与实验脚本
├── data/                    # 本地真实运行数据，不提交
├── .env.example             # 配置示例
├── Dockerfile
└── requirement.txt
```

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

### 2. 准备配置和人员名单

保留已有实际配置，首次使用时复制示例：

```bash
test -f .env || cp .env.example .env
mkdir -p data/user
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
| 人员文件 | `REVIEWER_USER_FILE`，默认 `data/user/users.yaml`。 |

在 `data/user/users.yaml` 创建人员名单，并将示例密钥替换为独立随机密钥：

```yaml
users:
  - name: "审核员姓名"
    secret_key: "replace-with-a-unique-random-secret"
```

```bash
chmod 600 data/user/users.yaml
```

人员文件缺失或格式不合法会阻止启动。环境变量优先于 `.env`；模型调用实际使用各自的 `VLLM_` 配置。完整参数见[运行配置](description/capability/application/runtime.md)、[向量服务](description/capability/infrastructure/myvector.md)、[多模态服务](description/capability/infrastructure/mllm.md)和[人员系统](description/capability/application/personnel.md)。

### 3. 启动开发数据库与中间件

数据库由同级部署项目管理：

```bash
cd ../registry-deploy
test -f .env || cp .env.example .env
```

填写部署 `.env` 中的 MySQL root 密码、业务账号密码和数据库信息，确保后端 `.env` 使用相同的业务连接信息，然后执行：

```bash
docker compose config --quiet
docker compose up -d --build --wait mysql
cd ../registry-be
```

新空卷由容器初始化正式资源表、向量缓存表和 MyVector 插件，后端不负责创建 MySQL 表。已有数据库升级须按[迁移说明](description/architecture/data/resource.md#旧库升级)操作；SQLite 在应用启动时自动升级。

中间件由独立项目部署，默认本机地址为 `http://127.0.0.1:22000`。启动后端前确认中间件及模型服务已准备好。

### 4. 启动后端

```bash
python app/main.py
```

也可直接在 IDE 中运行该文件。默认监听 `127.0.0.1:23000`，开启 Python 代码热重载：

- Swagger：<http://127.0.0.1:23000/docs>
- OpenAPI：<http://127.0.0.1:23000/openapi.json>
- 业务接口前缀：`/registry/api`

`.env` 和人员 YAML 修改后需要手动重启。代码重载后会重新登录中间件，可能需要等待旧会话过期；已有人员免登码也会失效。

---

## 容器部署

统一部署使用 [registry-deploy/docker-compose.yaml](../registry-deploy/docker-compose.yaml)，联动 MySQL 与后端。在后端 `.env` 和部署 `.env` 已准备好的基础上，从本项目根目录执行：

```bash
cd ../registry-deploy
test -f user.yaml || cp user.yaml.example user.yaml
```

编辑 `user.yaml` 替换示例人员及密钥，并确保容器 UID/GID `10001` 可读取该文件，然后启动：

```bash
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
```

容器工作目录为 `/workspace`，内部端口为 `8000`，默认映射到宿主机 `127.0.0.1:23000`。Compose 使用命名卷持久化 MySQL 和后端运行数据，并单独只读挂载人员名单。

本机开发与容器使用不同的文件存储位置，已有本机数据不会自动进入命名卷。不要同时启动使用相同中间件平台身份的本机后端和容器后端。配置覆盖、目录权限、备份及迁移见[统一部署说明](../registry-deploy/readme.md)。

---

## 接口与认证

通过 `POST /registry/api/auth/login` 提交人员 `secret_key`，取得 `login_code`。其他业务接口使用以下请求头，最终审核人由服务端登录身份确定：

```text
Authorization: Bearer <login_code>
```

| 接口领域 | 文档 |
| --- | --- |
| 登录与人员身份 | [人员登录 API](description/api/auth.md) |
| 平台列表、下次同步时间 | [平台查看 API](description/api/platform.md) |
| 正式文件基本信息与通行证 | [文件目录 API](description/api/catalog.md) |
| 正式原件、待入库原件 | [文件读取 API](description/api/resource.md) |
| 待入库列表、审核历史、最终决定 | [入库审核 API](description/api/ingestion-review.md) |
| 待删除列表、审核历史、最终决定 | [删除审核 API](description/api/deletion-review.md) |

完整路径、请求响应和错误语义以 [API 参考](description/api/readme.md)及 OpenAPI 为准。中间件的平台令牌与人员免登码相互独立。

---

## 测试与验证

在已激活的 Python 环境、项目根目录下执行：

```bash
python -m unittest discover -s tests -v
```

默认测试使用模拟中间件与模型；真实 MySQL、MyVector 集成测试需要独立临时数据库，未提供端口时会跳过。准备独立插件容器及 `registry_test` 数据库后，通过 `REGISTRY_TEST_MYSQL_PORT`、`REGISTRY_TEST_MYVECTOR_PORT` 指定其端口运行；测试账号约定及初始化方式见 [MySQL 集成测试](tests/test_resource_mysql.py)和 [HNSW 集成测试](tests/test_passport_hnsw.py)。集成测试会创建、修改和删除测试表，不能指向开发或正式业务库。

测试临时数据放在 `tests/tmp/`，结束后清理；固定样本放在 `tests/fixtures/`，实验和联调产物放在 `experiment/`。`data/` 仅用于真实运行数据。规则见[测试与实验产物规范](description/standard/experiment.md)，历史验证见[实验入口](experiment/readme.md)。

---

## 当前运行边界

- 当前按单实例、单 worker 运行；人员免登码、消息处理锁和部分运行状态保存在进程内。
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
