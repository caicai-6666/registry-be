# 环境配置与容器运行

本文说明后端项目的环境变量、启动入口、镜像构建及文件忽略规则。Python 环境和依赖安装见 [Python 运行环境与依赖](dependencies.md)。

---

## 配置文件

- [.env.example](../../../.env.example) 是可提交的配置示例，保留变量名称与开发默认值，密码和密钥为空。
- `.env` 保存本地实际配置，由 Git 和 Docker 构建上下文忽略。
- [Dockerfile](../../../Dockerfile) 基于 Python `3.12.15-slim-bookworm` 安装项目依赖，以普通用户启动 Uvicorn。
- [.dockerignore](../../../.dockerignore) 排除本地凭据、运行数据、开发环境和文档。
- [.gitignore](../../../.gitignore) 排除本地配置、运行数据、缓存和编辑器文件，保留 `.env.example`。

首次配置时可在项目根目录执行：

```bash
cp .env.example .env
chmod 600 .env
```

填入与开发数据库一致的业务账号密码；数据库部署入口见 [MySQL 与统一部署](../infrastructure/mysql.md)。已有 `.env` 时保留实际配置，按需补充新增变量。

---

## 环境变量

| 变量 | 示例值 | 用途 |
| --- | --- | --- |
| `APP_ENV` | `development` | 应用环境标识。 |
| `MYSQL_HOST` | `127.0.0.1` | 本机开发使用的数据库地址。 |
| `MYSQL_PORT` | `3306` | MySQL 连接端口。 |
| `MYSQL_DATABASE` | `registry` | 业务数据库名称。 |
| `MYSQL_USER` | `registry` | 业务数据库账号。 |
| `MYSQL_PASSWORD` | 空 | 业务账号密码，实际值保存在 `.env`。 |
| `SQLITE_PATH` | `data/review-log/registry.db` | SQLite 审核数据库文件路径，相对项目根目录。 |
| `SQLITE_BUSY_TIMEOUT_SECONDS` | `5` | SQLite 锁等待上限秒数。 |
| `REVIEWER_USER_FILE` | `data/user/users.yaml` | 启动期审核人员 YAML 文件。 |
| `AUTH_LOGIN_CODE_TTL_SECONDS` | `3600` | 人员免登码滑动有效期秒数。 |
| `OPENAI_API_KEY` | 空 | 模型服务密钥。 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 模型服务地址。 |

MySQL 连接参数用于正式资源查询、入库与删除接收中的资源核对，以及人工批准后的正式写入。[正式文件目录 API](../../api/catalog.md)可在关闭后台消费时独立使用，首次请求时建立连接。[SQLite 基础设施](../infrastructure/sqlite.md)在应用启动时初始化两张审核表，入库接收与工作流会在 ACK 前保存审核记录；[人工入库审核服务](ingestion-review.md)与[人工删除审核服务](deletion-review.md)保存决定并恢复未完成处理，关闭消息消费也可审核已有申请。OpenAI 配置预留给后续功能。MySQL 超时配置以[数据库连接说明](../infrastructure/mysql.md#后端连接配置)为准。

中间件注册与心跳已接入应用启动流程，其配置由[中间件注册与心跳](../infrastructure/middleware-session.md)统一维护，并在启动时从进程环境或项目根目录 `.env` 读取。

[多模态模型基础设施](../infrastructure/mllm.md)使用独立的 `VLLM_MLLM_` 配置；模型名与地址已经配置，已接入入库工作流的文件判重与通行证推荐节点；在后台入库接收启用时由应用创建、共享和关闭模型客户端，支持异步调用与 PDF 页面引用。

[入库申请接收服务](ingestion-consumer.md)默认在中间件配置完整时启用，待审核文件保存到 `REVIEW_LOG_ROOT/tmp-file/ingestion`，正式资源才使用 `RESOURCE_ROOT`。[删除申请接收服务](deletion-consumer.md)通过 `DELETION_ENABLED` 独立启用，待审引用 resource 中的原文件，收到申请时不移动文件。删除保留目录暂不自动清理，完整规则见[审核文件存储](../infrastructure/review-file-storage.md)。

---

## 本机开发启动

启动前需准备独立的人员文件，缺失或不合法会阻止启动；配置契约见[人员系统](personnel.md)。

在项目根目录执行：

```bash
conda activate registry-be
python app/main.py
```

也可以在 IDE 中直接运行 `app/main.py`，或执行 `python -m app.main`。文件内启动使用单个 worker，监听 `127.0.0.1:23000`，默认开启代码热重载；通过 `app.main:app` 导入字符串加载应用，显式监控 `app/`，不依赖 IDE 的工作目录。应用执行人员加载、中间件注册、心跳、平台目录刷新、入库与删除接收、人工入库与删除审核恢复及停机清理生命周期；直接运行文件时按文件位置确定项目导入路径。

应用自行读取 `.env` 配置，入口为 `app/main.py` 中的 `app` 对象。直接启动时浏览器访问 `http://127.0.0.1:23000/docs` 查看接口文档，`http://127.0.0.1:23000/openapi.json` 返回当前应用 Schema。

也可以通过命令行开启自动重载：

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

代码自动重载会执行应用关闭和重新启动，并重新登录，可能需要等待旧中间件会话过期；人员免登码也会失效。`.env` 和人员 YAML 不在默认 Python 文件监控范围，修改后仍需手动重启。容器入口使用不带 reload 的 Uvicorn 命令。

---

## 镜像构建与运行

在项目根目录执行：

```bash
docker build -t registry-be:dev .
docker run -d --name registry-be-dev \
  --network registry_registry \
  --env-file .env \
  -e MYSQL_HOST=mysql \
  -e MIDDLEWARE_BASE_URL=http://host.docker.internal:22000 \
  -p 127.0.0.1:8000:8000 \
  -v registry-backend-data:/workspace/data \
  -v "$PWD/data/user:/workspace/data/user:ro" \
  registry-be:dev
```

容器内 Uvicorn 固定监听 `0.0.0.0:8000`。环境变量在运行时注入，`.env` 不复制进镜像；容器内 `APP_ENV` 默认是 `production`，通过 `--env-file` 可覆盖。

人员文件同样不进入镜像；上例独立只读挂载 `data/user/`。启动前必须确认容器 UID/GID `10001` 可读取其中的 `users.yaml`，可由部署管理员通过文件所有权或 ACL 授权。挂载内容不是有效人员文件时应用启动失败。

上述 Docker 命令直接读取 `KEY=value`。本地 `.env` 使用无外围引号的值，保证本机配置读取与 Docker 一致；Docker `--env-file` 不会按 dotenv 规则去除引号。

上例需先启动统一部署项目的 MySQL，使用默认项目名 `registry` 创建的 `registry_registry` 网络，通过 `mysql:3306` 连接数据库，并使用 Docker Desktop 访问宿主机开发中间件。其他部署环境需调整网络和中间件地址。当前服务按单个 worker 运行，避免同一平台的多个实例竞争中间件会话。

容器内项目根目录为 `/workspace`，应用包位于 `/workspace/app`。镜像以 UID/GID `10001` 运行，`/workspace/data` 可写。SQLite 已在应用启动时初始化，必须挂载该目录以持久化审核记录和文件；使用宿主机目录挂载时需确保运行用户具有写权限。

上例将 `/workspace/data` 挂载到命名卷 `registry-backend-data`，保存已确认申请的附件；重新创建容器时继续使用同一数据卷，并与 MySQL 数据一起保留。

查看运行状态与日志：

```bash
docker ps --filter name=registry-be-dev
docker logs -f registry-be-dev
```

镜像健康检查读取 `/openapi.json`，只检查 Web 入口是否可响应。

---

## 与统一部署的关系

后端已加入 `registry-deploy` 的 Compose `backend` 服务，构建上下文为 `../registry-be`，数据库连接固定为 `mysql:3306`，等待 MySQL 健康检查通过后启动。运行配置读取 `registry-be/.env` 并由部署环境覆盖数据库及容器目录。

Compose 使用 `backend-data:/workspace/data` 持久化运行数据，另将部署目录的 `user.yaml` 只读挂载为 `/workspace/data/user/users.yaml`，缺失文件时不自动创建目录。完整准备与启动步骤见[统一部署说明](../../../../registry-deploy/readme.md#人员名单挂载)。该方式与上面的手工 `docker run` 示例分别使用独立数据卷，不会自动互相迁移数据。

容器内的 `127.0.0.1` 指向后端容器自身，不能直接使用本机开发的 MySQL 地址连接其他容器。

镜像基础参考 [Python 官方镜像说明](https://hub.docker.com/_/python)。
