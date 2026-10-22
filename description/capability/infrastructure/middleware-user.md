# 中央用户认证与资料维护边界

> **用途：** registry 直接以用户 token 查询中间件身份并续期，不依赖知识后端的 verify 接口。用户全表注册和登录写入由知识后端负责；用户资料、权限更新与管理员维护仍归 registry。

---

## 中央身份查询

`app/infrastructure/middleware.py` 的 `touch_user_login(access_token, token)` 调用 `POST /middleware-service/api/user/login-cache`。header 使用 registry 的平台 Bearer token，请求体只有用户 token：

```json
{"access_token":"replace-with-user-access-token"}
```

有效返回 `{"userid":"user-001","username":"审核员甲","is_logged_in":true,"ttl":90}`；未知、过期或被新登录替换时返回 `{"userid":null,"username":null,"is_logged_in":false,"ttl":0}`。有效期以中央实际返回为准，不在 registry 写死 90 秒。DTO 严格验证布尔、整数、非空 userid、username 和状态一致性，拒绝旧的离线 userid 响应。

`app/service/auth.py` 的 `AuthService.resolve_user` 每次查询中央，直接使用可信 userid 和 username 构造最小身份，不查询员工表。不保留用户 token 到 userid 的进程映射、不使用本地固定 TTL、不缓存认证成功。中央确认离线返回 HTTP 401；依赖故障返回 503。中间件的 HTTP 401 表示平台凭据失效，只失效本次使用的平台 token，不能误判为用户退出。日志仅记录异常类型及状态码，不输出用户 token、平台 token 或上游正文。

开发模拟身份和生产身份采用相同路径，不需要本地员工记录；用户名以中央当前值为准，不创建员工或补权限。普通业务与 `/auth/me` 采用同一校验，无隐式重新登录路径。

---

## 注册与登录写入职责

知识后端在可信登录完成后，以自身平台凭据调用 `PUT /user/login-cache`，提交 userid 和本次签发的 access_token；中央保证同一用户只有一个当前 token，新登录使旧 token 立即失效。registry 客户端不再提供该写接口和用户 full-table 方法，应用启动不发布用户总表，也不建立开发模拟登录。

本项目保留 `MiddlewareClient.update_user_info` 对接 `POST /user/update`，用于 registry 人员资料和权限维护。[权限管理](../application/user-access.md)发送当前完整permissions映射，省略username，并通过admins/add或remove同步指定平台管理员。[手动刷新](../application/personnel-refresh.md)在数据库提交后仅发送变化姓名，不发送 permissions、不调用全表或登录写入接口。平台管理员总表发布已迁至知识后端，registry 的 [PlatformAdminService](../application/authorization.md#管理员资料与同步) 只查询管理权和展示资料。

两后端必须使用同一权威人员与权限数据源，权限更新需要先持久化；中央用户全表的旧快照可能覆盖并发增量更新，进程内锁不能解决跨后端竞态。本轮未实现源版本条件发布或跨后端串行机制，真实切换仍需处理该边界，不能将本地 token 联调视作一致性已解决。

---

## 配置与装配

- `AUTH_REQUEST_TIMEOUT_SECONDS`：中央用户查询的总超时，默认 5 秒。
- 旧 `MIDDLEWARE_USER_SYNC_*`、开发登录、钉钉及头像配置均已清理，不再提供兼容别名。
- [平台会话配置](middleware-session.md)提供中间件地址和 registry 凭据；缺少有效平台会话时认证返回 503。

`app/main.py` 装配中央认证、人员仓库及管理员服务，不加载旧的开发登录或钉钉凭据，也不创建本地用户会话缓存。实际 `.env` 已移除管理员全表同步开关，其他连接和业务配置保留。

---

## 验证边界

`tests/test_middleware_user.py` 覆盖精确请求体、空 userid 离线契约、严格响应、平台 401、用户名缺失/离线矛盾拒绝、每次查询及跨认证实例复用中央 token。`tests/test_central_auth_api.py` 使用隔离 HTTP 中央替身验证真实应用装配、滑动续期、旧 token 拒绝、401/403/503、registry 不发送用户或管理员 full-table，也不发送 PUT。中央原子替换由中间件实现，替身验证不代替真实 Redis、钉钉或多平台联通验证。

`tests/test_shared_login_token.py` 是可选跨项目隔离补验：用知识后端独立 Python 进程执行实际签发与 PUT 适配，再由 registry 实际查询客户端消费中央绑定的 token，验证新旧 token 区分及 registry 重建实例可继续查询。需要同级知识后端及其 Python 环境，可通过 `KNOWLEDGE_AUTH_PYTHON` 指定；测试只使用内存 HTTP 替身，不运行目标应用生命周期、不写目标文件或连接共享服务。
