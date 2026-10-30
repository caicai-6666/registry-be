# 注册中心管理员授权

> **用途：** registry 管理接口统一要求中央用户登录有效，并属于当前注册中心平台管理员。用户登录、首次初始化和头像由知识后端负责。

---

## 依赖链与统一边界

`app/router/dependency.py` 提供普通登录入口 `require_user` 和管理入口 `require_platform_admin`（参数注入名 `PlatformAdminUserDependency`）。管理入口复用普通登录依赖，先解析用户 Bearer，通过 `AuthService.resolve_user` 向中间件确认真实已注册的中央用户和有效登录，再使用 registry 平台会话调用 `GET /middleware-service/api/platform/admins/list`，按可信 userid 验证管理权。第一步未通过，不取得管理员服务或查询名单。

`app/main.py` 创建带 `Depends(require_platform_admin)` 的业务路由组，auth、user、platform、catalog、resource、ingestion-review、deletion-review 均加入此组；user 已提供用户/部门列表与手动刷新。新管理路由也必须加入该组；正式文件的 `/resource/authorized-file/{resource_id}` 在组外单独挂载，显式注入登录与文件授权依赖；服务间 `/resource/service-authorized-file/{resource_id}` 同样独立挂载，显式验证知识后端平台令牌及员工文件权限。OpenAPI、Swagger 和 ReDoc 是框架文档入口，不承担业务操作，继续公开用于文档和容器 HTTP 探针。

| 依赖 | 用途 |
| --- | --- |
| `AuthenticatedUserDependency` | 仅验证中央 token 对应的有效用户，不赋予管理权。 |
| `ReadableResourceDependency` | 登录后读取正式资源与本地员工文件权限，按来源平台比较等级。定义于 `app/router/resource.py`。 |
| `PlatformAdminUserDependency` | 管理认证依赖；验证登录与 registry 管理权，返回可信管理员身份。 |

```python
from app.router.dependency import PlatformAdminUserDependency

@router.get('/management')
async def management(user: PlatformAdminUserDependency):
    return {'userid': user.userid}
```

认证统一在主业务路由组声明，子路由不再重复声明；当前用户查询和人工审核均复用同一依赖结果，审核接口直接取该用户的 userid；不另设审核员认证依赖。FastAPI 在一次请求内使用户查询与管理员查询各执行一次。当前身份接口也要求管理员，不会创建或恢复登录。

---

## 普通用户正式文件授权

[普通用户原件接口](../../api/resource.md#按用户权限获取正式文件原件)复用 `require_user`，随后由 `require_file_access` 比较本地权威员工权限和正式资源当前等级。只查询员工权限 JSON，不 JOIN 部门或管理员关系；中央身份提供 userid，本地数据库提供当前权限。员工不存在、缺少来源平台键、文件等级未知或用户等级不足时返回403。显式0与缺键不同；管理员身份不能绕过本入口的文件等级。

授权成功后复用既有正式文件安全读取及媒体响应，不读取审核暂存或删除保留目录。失败保留401/403/404/503语义，不从 token 内客户端声明或旧登录权限恢复授权。此入口单独挂载，不改变任何现有管理路由。

---

## 服务间正式文件授权

[可信后台原件接口](../../api/resource.md#可信后台代用户获取正式文件原件)供知识后端的助理机器人和定时任务使用。`ServiceFileAuth` 复用registry现有平台会话和 `MiddlewareClient`，调用中间件 `POST /platform/token/verify`：Authorization携带registry自身令牌，JSON仅携带待验证的调用方access_token。严格验收valid/code及二者一致性，有效身份必须为固定平台 `knowledge-accumulation`；其他有效平台（含registry）返回403。无效调用方令牌返回401；依赖故障、registry自身令牌失效、接口尚未部署时返回503，禁止降级为裸userid、heartbeat或用户身份猜测。

调用平台通过认证后，body中的userid才作为可信后台绑定的任务所属员工；模型不能选择该字段。`require_service_file_access` 与用户登录入口共用 `check_employee_file_access`，实时读取SQL权限并按正式资源来源平台与当前file_level比较。既不需要用户token，也不创建/续期用户登录、提升文件权限或绕过文件等级。平台认证和文件权限均不缓存；授权后复用原件安全读取。请求失败不回显令牌、路径或上游正文。

组合根在既有HTTP客户端生命周期中装配认证器，无新增客户端或共享密钥；超时复用 `AUTH_REQUEST_TIMEOUT_SECONDS`。中间件上游401仅失效本次registry平台token，由现有平台会话任务重登，不触碰调用平台会话。需先部署中间件只读校验能力；接口契约以[文件API](../../api/resource.md#可信后台代用户获取正式文件原件)为准。

本地 `tests/test_service_authorized_file.py` 使用内存HTTP/SQL/原件替身，覆盖令牌隔离、只读验证精确载荷、无效/其他平台、失败关闭、SQL实时撤权、来源平台隔离、显式0、未知文件等级、缺失原件、严格body、旧用户入口兼容及脱敏错误。不启动应用生命周期，不读写 `data/`；测试按项目规则本地保留。

---

## 管理员与文件等级

管理目标来自 `MIDDLEWARE_PLATFORM_CODE`，通常为 `registry`。子平台管理员、员工资料中的 managed_platforms 展示字段，以及任意文件访问等级，都不能替代中央确认的注册中心管理权。

管理员正式原件与待审原件入口仍先校验管理权；普通用户只能通过独立的文件权限入口下载正式原件。文件等级不赋予任何管理接口访问权。

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
| `AUTH_REQUEST_TIMEOUT_SECONDS` | `5` | 用户验证总超时、单轮管理员查询及服务间平台校验超时。 |

旧开发登录、钉钉登录、本地头像存储和全表同步配置已移除；现有通讯录刷新与 HTTP 头像代理配置分别由对应能力维护。进程环境优先于 `.env`，不执行插值，变更后需重启或重建服务。

| 状态码 | 含义 |
| --- | --- |
| `401` | 用户 Bearer 缺失、无效、过期或被新登录替换；不查询管理员。 |
| `403` | 中央用户有效，但不是当前注册中心管理员；不执行后续业务。 |
| `503` | 平台会话未就绪、中央用户或管理员依赖异常；不回退放行。 |

中间件平台401只失效本次使用的平台 token，不能作为用户离线处理。日志不输出凭据或上游正文。人员同源与跨后端旧全表覆盖增量更新的边界见[中央用户认证](../infrastructure/middleware-user.md)。

---

## 验证

`tests/test_management_dependencies.py` 覆盖管理路由的 401/403/503 与 OpenAPI 403 声明；`tests/test_resource_api.py` 验证文件等级足够仍不能绕过管理员；`tests/test_platform_api.py` 验证普通用户不能查看平台。中间件精确查询契约、实时管理员名单和无总表写入由 `tests/test_platform_admin.py` 验证。测试使用隔离身份、HTTP替身和 tests/tmp，不改共享数据。

`tests/test_authorized_resource.py` 覆盖普通用户精确/更高等级、显式0与缺键、来源平台隔离、未知等级、管理员不能旁路、权限撤销/文件等级变更即时生效、中央/数据库故障、原件读取及单表权限严格校验。测试不连接共享服务，文件与数据库使用 tests/tmp。
