# 人员系统

> **用途：** 参照合同服务的 YAML 人员目录与进程内免登码方案，为注册中心提供独立的审核人身份基础设施。

---

## 职责与装配

`app/user/` 定义不可变人员模型并严格加载配置，`app/service/auth.py` 校验密钥、签发免登码和解析人员；`app/router/auth.py` 提供[登录 API](../../api/auth.md)。文件字段由[人员定义](../../architecture/data/reviewer-user-definition.md)维护。

`create_app()` 的生命周期先加载人员文件，再初始化 SQLite、文件目录和中间件服务。人员文件不合法时直接阻止启动，不自动生成默认账号或复用合同服务的凭据。人员快照与认证服务分别保存在 `app.state.reviewer_user_catalog`、`app.state.auth_service`。

后续受保护路由使用以下依赖：

```python
from app.router.dependency import ReviewerUserDependency

async def review(reviewer: ReviewerUserDependency):
    # reviewer 为服务器根据 Bearer 免登码解析出的审核人名称。
    ...
```

如需完整人员对象，使用 `AuthenticatedUserDependency`。[入库审核接口](../../api/ingestion-review.md)通过 `ReviewerUserDependency` 取得可信审核人名称。后台消费者不使用人工免登码。

---

## 配置与文件准备

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `REVIEWER_USER_FILE` | `data/user/users.yaml` | 启动期人员文件路径。 |
| `AUTH_LOGIN_CODE_TTL_SECONDS` | `3600` | 免登码滑动有效期，正整数秒。 |

配置按“进程环境优先于 `.env`”读取，不进行 dotenv 插值。应用不会自动创建人员文件；本机文件已经单独准备，密钥应直接在本地文件查看或更换，不在文档和日志中公开。

新增环境时按[人员定义](../../architecture/data/reviewer-user-definition.md)创建文件，每人分配独立随机密钥，并限制文件读取权限：

```bash
mkdir -p data/user
# 在本地编辑 data/user/users.yaml，填写人员名称与独立随机密钥。
chmod 600 data/user/users.yaml
```

容器不包含此文件，需挂载到配置指向的位置。默认容器运行用户为 UID/GID `10001`，挂载文件必须允许该用户读取。挂载方式见[容器运行](runtime.md#镜像构建与运行)。

统一部署沿用合同项目方式：部署目录 `user.yaml` 只读挂载到 `/workspace/data/user/users.yaml`；该文件与本机 `data/user/users.yaml` 独立，修改不会自动同步。部署模板、初始化和重建说明见[统一部署人员挂载](../../../../registry-deploy/readme.md#人员名单挂载)。

---

## 免登码与实现边界

`LoginCodeCache` 用安全随机数签发免登码，以异步锁保护进程内映射。成功身份解析会将过期点刷新为当前单调时钟加 TTL；达到过期点后不会复活，签发和解析时清除过期条目。重复登录签发新码，同时保留仍有效的旧码。

缓存不持久化、不跨进程共享，重启后所有旧码失效；当前应使用单个 worker。没有登出或主动撤销接口，修改密钥并重启会使原快照和免登码全部失效。

请求体错误只返回错误类型、位置和说明，不回显密钥及其他输入。线上凭据通过 HTTPS 或可信反向代理传输。

---

## 验证

```bash
python -m unittest tests.test_auth tests.test_middleware_session tests.test_sqlite -v
```

测试显式注入 `tests/tmp/` 中的人员文件、审核数据库和文件目录，验证唯一性、非法配置、凭据遮蔽、滑动过期、并发签发、Bearer 身份、重启失效及启动失败边界，不读取真实人员密钥。
