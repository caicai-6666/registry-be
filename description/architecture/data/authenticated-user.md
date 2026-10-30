# 登录身份契约

> **用途：** 描述中央用户 token 验证后的 registry 服务端身份。接口见[当前身份 API](../../api/auth.md)，模块与配置见[人员系统](../../capability/application/personnel.md)。

---

## 字段定义

`app/auth/model.py` 的 `RegistryAdminIdentity` 是 `/auth/me` 唯一响应结构：

| 字段 | 含义 |
| --- | --- |
| `userid` | 中间件验证用户 token 后返回的可信标识，不取客户端 userid。 |
| `username` | 同一次中央登录查询返回的当前用户名，不从本地员工表补取。 |

该接口先验证中央登录，再验证 registry 管理员名单；成功响应只有上述两项，不包含部门、头像、权限等级或 managed_platforms。完整用户资料由知识后端提供。

内部依赖仍使用 `AuthenticatedUser`，认证阶段只填入中央 userid 和 name（即 username），其余可选字段不作为本次登录的资料或授权来源。该类型也供员工仓库映射使用，不能因此把仓库字段重新暴露到 `/auth/me`。认证不查询员工数据库，模拟身份无需在 SQL 中存在；中央有效用户名缺失或状态矛盾返回 503。

---

## 审核与文件授权

人工审核以可信 userid 写入 reviewer，历史查询默认使用当前 userid。自动审核继续使用固定名称“注册中心自动审核员”。文件访问等级与管理权独立：当前身份、平台列表、审核及管理员原件入口要求 registry 管理员；独立的用户原件入口验证中央用户 token 后，按文件来源平台比较实时 SQL 权限和文件等级。可信后台原件入口验证知识后端平台 token 及任务绑定的员工权限，不使用或续期用户登录。头像在知识后端直接读取只要求正常登录，registry 头像代理仍要求管理员。详见[授权依赖](../../capability/application/authorization.md)。

---

## 会话来源

登录签发由知识后端负责，registry 不缓存用户 token 到 userid 的映射或固定过期时间。每次访问直接以 token 查询中间件并续期，新登录替换旧 token 后，下次旧 token 访问即返回 401。registry 重启不影响中央用户会话；中央故障返回 503，不回退本地验证。请求体、响应与故障语义统一见[中央用户认证](../../capability/infrastructure/middleware-user.md)。
