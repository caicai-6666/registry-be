# 注册中心管理员授权

> **用途：** 所有 registry 业务接口统一要求中央用户登录有效，并属于当前注册中心平台管理员。用户登录、首次初始化和头像由知识后端负责。

---

## 依赖链与统一边界

`app/router/dependency.py` 只提供一个业务认证入口 `require_platform_admin`（参数注入名 `PlatformAdminUserDependency`）。它先解析用户 Bearer，通过 `AuthService.resolve_user` 向中间件确认真实已注册的中央用户和有效登录，再使用 registry 平台会话调用 `GET /middleware-service/api/platform/admins/list`，按可信 userid 验证管理权。第一步未通过，不取得管理员服务或查询名单。

`app/main.py` 创建带 `Depends(require_platform_admin)` 的业务路由组，auth、user、platform、catalog、resource、ingestion-review、deletion-review 均加入此组；user 已提供用户/部门列表与手动刷新。新业务路由也必须加入该组。OpenAPI、Swagger 和 ReDoc 是框架文档入口，不承担业务操作，继续公开用于文档和容器 HTTP 探针。

| 依赖 | 用途 |
| --- | --- |
| `PlatformAdminUserDependency` | 唯一认证依赖；验证登录与 registry 管理权，返回可信管理员身份。 |

```python
from app.router.dependency import PlatformAdminUserDependency

@router.get('/management')
async def management(user: PlatformAdminUserDependency):
    return {'userid': user.userid}
```

认证统一在主业务路由组声明，子路由不再重复声明；当前用户查询和人工审核均复用同一依赖结果，审核接口直接取该用户的 userid；不另设审核员认证依赖。FastAPI 在一次请求内使用户查询与管理员查询各执行一次。当前身份接口也要求管理员，不会创建或恢复登录。

---

## 管理员与文件等级

管理目标来自 `MIDDLEWARE_PLATFORM_CODE`，通常为 `registry`。子平台管理员、员工资料中的 managed_platforms 展示字段，以及任意文件访问等级，都不能替代中央确认的注册中心管理权。

正式原件和待审原件均先验证管理员，再查询资源和读取磁盘。旧的“文件等级足够即可读取”规则已从本后端移除。文件等级字段保留用于资源元数据及审核结果，不因此给普通用户开放管理接口。

---

## 管理员资料与同步

`PlatformAdminService.list_admin_profiles()` 从权威 MySQL 管理员关联和员工表取得管理员 userid、展示名称及头像文件名，失败返回 503；不发布模拟身份或从开发配置合并管理权。头像引用转换由 `app/model/avatar.py` 提供，不引入头像读取或转码能力。

管理员总表发布迁至知识后端，由其统一合并权威 MySQL 管理员关系与开发模拟身份配置。registry 不再启动同步任务，发布适配和同步开关均已移除；启动和鉴权都不会覆盖中央管理员。中间件写接口授权由维护方同步，迁移代码不代表真实发布已启用。

知识后端进程内维护锁不能保护 registry 的并发管理编辑；管理员旧全表同样可能覆盖新的增量关系，真实启用前应统一权威数据源并安排切换和写入协调。

员工仓库由组合根统一创建和关闭；本服务不创建独立仓库或开发降级身份。[用户授权管理](user-access.md)已提供文件等级与管理员关系编辑，提交MySQL后即时调用中间件user/update和admins/add/remove。

---

## 配置与错误

`app/config/authorization.py` 的 `AuthorizationSettings` 只读取以下一项：

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `AUTH_REQUEST_TIMEOUT_SECONDS` | `5` | 用户验证总超时，以及单轮管理员查询超时。 |

旧开发登录、钉钉、头像和用户同步配置及其别名均已移除。进程环境优先于 `.env`，不执行插值，变更后需重启或重建服务。

| 状态码 | 含义 |
| --- | --- |
| `401` | 用户 Bearer 缺失、无效、过期或被新登录替换；不查询管理员。 |
| `403` | 中央用户有效，但不是当前注册中心管理员；不执行后续业务。 |
| `503` | 平台会话未就绪、中央用户或管理员依赖异常；不回退放行。 |

中间件平台401只失效本次使用的平台 token，不能作为用户离线处理。日志不输出凭据或上游正文。人员同源与跨后端旧全表覆盖增量更新的边界见[中央用户认证](../infrastructure/middleware-user.md)。

---

## 验证

`tests/test_management_dependencies.py` 覆盖全部17个业务接口的401/403/503与OpenAPI403声明；`tests/test_resource_api.py` 验证文件等级足够仍不能绕过管理员；`tests/test_platform_api.py` 验证普通用户不能查看平台。中间件精确查询契约、实时管理员名单和无总表写入由 `tests/test_platform_admin.py` 验证。测试使用隔离身份、HTTP替身和 tests/tmp，不改共享数据。
