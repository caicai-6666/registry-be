# 人员身份与职责

> **用途：** 说明用户登录迁移后的 registry 职责。用户在 `knowledge-accumulation-be` 登录；registry 直接查询中央用户 token，并使用员工目录和管理员关系执行管理与文件读取授权。

---

## 职责与装配

知识后端负责员工首次初始化、用户与平台管理员全表注册和可信登录后的 token 签发与中央写入。registry 负责后续人员权限、资料增量更新与平台管理员维护；不再装配钉钉登录、固定开发 token 验证、本地用户缓存或用户全表同步。

`app/main.py` 创建 `AuthService`、员工仓库和平台管理员服务。`AuthService` 每请求查询中间件 `POST /user/login-cache`，直接采用返回的可信 userid 与 username。用户 token 与平台 token 分离，查询本身完成中央续期；详情见[中央用户认证](../infrastructure/middleware-user.md)。

[当前身份 API](../../api/auth.md)只查询已有登录，不允许借身份查询恢复过期 token。登录模式、开发登录凭据和钉钉登录凭据在知识后端配置，registry 不再读取这些登录认证配置；旧开发与钉钉认证、用户会话缓存、员工初始化、头像读写代码及配置均已删除。

---

## 资料与授权

[用户授权管理](user-access.md)在单一事务中保存指定用户的文件等级和管理员关系，再即时增量同步中间件；省略的平台保留，失败可重复提交重试。

[员工资料刷新](personnel-refresh.md)使用 registry 独立 DINGTALK_* 通讯录配置，管理员手动更新已有员工的姓名和部门；仅姓名变化调用 user/update，不触发全表注册或登录。

[用户列表](../../api/user.md#获取用户列表)只读取 employees 五个存储字段，不关联部门或管理员表；部门名称由前端匹配，头像路径转为文件名，权限 JSON 返回对象。

[部门列表](../../api/user.md#获取部门列表)读取 MySQL departments，返回 ID 和名称，按 ID 排序；仅注册中心管理员可访问，不触发钉钉同步。

[员工仓库](../../architecture/data/employee.md)提供权威资料、文件等级映射与管理员关联展示。两后端须对齐同一权威人员数据源；不能分别维护两份权限。认证不查员工表；`/auth/me` 只返回中央 userid 与 username，不暴露完整人员资料。员工仓库仅用于独立的管理和资料展示查询，其故障不影响身份验证。

所有业务路由统一使用 `PlatformAdminUserDependency`，内部顺序完成中央登录与注册中心管理员校验，不提供单独的仅登录认证依赖。人工审核接口直接从该认证结果取可信 userid 记录审核员，不另设审核员认证依赖。文件访问等级与管理权独立，具体见[授权依赖](authorization.md)。

头像保存与磁盘读取均由知识后端负责，registry 可通过带管理员鉴权的 HTTP 代理转发读取，接口为 `GET /knowledge-accumulation/api/resource/avatar/{filename}`，仅要求登录；平台目录等管理员展示中的头像字段仍是文件名，迁移与目录边界见[头像迁移说明](../infrastructure/avatar.md)。

---

## 验证与切换

测试使用 `tests/tmp/` 以及显式 HTTP、人员仓库替身，不连接真实钉钉或发布共享总表。业务路由测试通过 `tests/support.py` 安装测试身份，不在应用中保留离线开发放行逻辑。

知识后端的登录侧修正由其主开发助手负责；本项目实际 `.env`、中间件和其他项目未修改。本地认证回归不表示生产授权、人员同源或跨后端全表/增量并发边界已完成切换。
