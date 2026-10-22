# 中间件注册与心跳

本模块在后端启动时与 `middleware-service` 建立注册中心平台会话，并在运行期间通过心跳续期。对外契约以中间件项目的[平台认证 API](../../../../../知识沉淀中间件服务/middleware-service/description/api/platform.md)为准。

> **注册边界：** 此处注册指白名单平台使用密钥登录并建立活跃会话，不会新增或修改中间件的平台白名单。

---

## 职责与生命周期

- `app/config/middleware.py`：加载并校验中间件配置。
- `app/infrastructure/middleware.py`：封装异步登录、心跳与响应校验。
- `app/service/middleware_session.py`：维护进程内令牌、有效期、心跳和重新登录。
- `app/main.py`：通过 FastAPI lifespan 启动后台任务，退出时取消任务并关闭 HTTP 客户端。

应用通过 `app.state.middleware_session_service` 保存会话服务，内部业务可以调用 `get_access_token()` 取得尚未过期的 `SecretStr` 令牌；读取不续期。收到业务请求的 401 时，可调用 `invalidate_access_token(token)` 失效对应令牌，避免迟到响应清除新会话。

启动只创建后台任务，不等待中间件登录成功。短时不可用时 Web 服务仍可响应，但需要中间件身份的后续业务必须等待有效令牌。令牌仅保存在内存，不写入 `.env`、数据库或对外响应。

---

## 配置

配置在应用启动时读取：进程环境变量优先，其次项目根目录 `.env`，最后为代码默认值。直接读取 `.env` 时不展开变量引用，不修改进程环境；修改配置后需重启应用。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MIDDLEWARE_BASE_URL` | 空 | HTTP/HTTPS 服务根地址，包含端口，不附加接口路径、查询参数或凭据。 |
| `MIDDLEWARE_PLATFORM_CODE` | `registry` | 白名单平台代号，格式为小写 kebab-case。 |
| `MIDDLEWARE_SECRET` | 空 | 白名单中对应平台的密钥，保留原值。 |
| `MIDDLEWARE_RETRY_INTERVAL_SECONDS` | `30` | 请求失败后的重试间隔，有限正数，单位秒。 |
| `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS` | `40` | 单次完整请求的总超时，有限正数，单位秒。 |

[.env.example](../../../.env.example) 提供本机开发地址 `http://127.0.0.1:22000` 和空密钥。当前本地 `.env` 使用已有中间件白名单中 `registry` 的密钥，实际值不进入文档或版本控制。

地址或密钥未填写时不启动连接；非法地址、代号或时间配置会阻止应用启动。请求超时默认预留中间件登录建立消息客户端所需的时间，该过程最长约 30 秒。

容器应配置容器内可访问的中间件地址；Docker Desktop 连接宿主机开发服务可使用 `http://host.docker.internal:22000`。跨容器部署时使用所在网络中的服务地址，不使用容器自身的 `127.0.0.1`。

---

## 请求与调度

1. 后台发送 `POST /middleware-service/api/platform/login`，JSON 请求体仅包含 `platform_code` 和 `secret`。
2. 成功响应必须包含非空令牌、`token_type=bearer`、匹配的平台代号、正整数有效期和小于有效期的心跳间隔。
3. 按服务端返回的间隔发送 `POST /middleware-service/api/platform/heartbeat`，携带 `Authorization: Bearer <access_token>`，不发送请求体或用户数据。
4. 每次心跳成功后更新有效期与下次间隔，令牌保持不变。

管理员总表发布、用户全表注册和登录写入均由知识后端负责；registry 不启动全表同步。[中央用户查询](middleware-user.md)使用用户 token，平台心跳不参与用户资料、登录状态或 TTL 同步。

客户端不跟随重定向、不使用环境代理。成功响应必须为 200，非法响应不会被当作已注册或已续期。有效期采用请求开始时的单调时间保守估算，网络耗时不计入额外有效期。

---

## 故障与停机

| 情况 | 行为 |
| --- | --- |
| 登录失败、503、超时、断线或非法响应 | 等待配置的重试间隔，再次登录。 |
| 登录返回 409 | 保留服务端已有会话，等待重试；不尝试踢出其他实例。 |
| 心跳返回 401 | 清除本地令牌，等待后重新登录。 |
| 心跳临时失败 | 有效期内保留令牌并重试；重试等待不超过剩余有效期。 |
| 本地令牌过期 | 停止向内部业务提供旧令牌，下一轮重新登录。 |
| 应用关闭 | 取消在途请求和后台任务，清除令牌，关闭 HTTP 连接池。 |

日志只记录阶段、异常类型与 HTTP 状态码，不记录密钥、令牌、响应正文或异常原文。

> **实例边界：** 同一平台仅允许一个活跃会话。当前按单个 Uvicorn worker、单个服务实例运行；中间件暂无登出接口，立即重启或新实例登录可能暂时收到 409，需等待旧会话自然过期。

---

## 验证与当前范围

执行：

```bash
conda activate registry-be
python -m unittest discover -s tests -v
```

测试覆盖无请求体心跳、服务端间隔更新、409 重试、401 重登、断线和临时故障、过期边界、总超时、关闭取消、配置优先级、响应校验和敏感日志边界。

本模块维护平台登录与心跳，[入库申请接收服务](../application/ingestion-consumer.md)已复用其令牌拉取并确认申请。人工审核与自动拒绝已复用该令牌发布审核结果。Web 镜像健康检查只表示 HTTP 入口可响应，不代表中间件会话有效。
