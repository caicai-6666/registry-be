# 当前用户身份 API

注册中心验证中央用户会话及当前注册中心管理员资格。用户登录和 token 签发由 `knowledge-accumulation-be` 负责；各平台可以直接向中间件查询用户 token，无需调用知识后端的身份验证接口。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取当前身份](#获取当前身份) | `GET` | `/knowledge-accumulation/registry/api/auth/me` |

---

## 获取当前身份

`GET /knowledge-accumulation/registry/api/auth/me`，确认已登录的注册中心管理员身份；普通用户返回403。

**认证方式：** header `Authorization: Bearer <access_token>`，使用知识后端签发的用户 token。与 registry 调用中间件时使用的平台 token 不同。

**请求参数：** 无 path、query 参数或请求体。不能通过 userid 指定当前用户。

**成功响应：** `200 application/json`，携带 `Cache-Control: no-store`：

```json
{"userid":"user-001","username":"审核员甲"}
```

只返回 `userid` 和 `username`，均来自中间件 `POST /middleware-service/api/user/login-cache`；不返回部门、头像、文件权限或管理平台列表。每次验证中央登录并续期后，再确认 registry 管理权限；不查询本地员工表，开发模拟身份也使用中央用户名。完整用户资料由知识后端 `/knowledge-accumulation/api/auth/me` 提供。字段见[身份契约](../architecture/data/authenticated-user.md)。

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | Bearer 头缺失或格式错误，或中央确认 token 未知、过期、已被新登录替换。携带 `WWW-Authenticate: Bearer`。 |
| `403` | 用户登录有效但不是注册中心管理员。 |
| `503` | registry 平台会话未就绪、中央用户或管理员查询网络/响应异常。不会回退到本地凭据。 |

**请求示例：**

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/auth/me' \
  -H 'Authorization: Bearer replace-with-user-access-token'
```

**行为与边界：** 有效查询由中间件滑动续期；本接口不创建、恢复或签发登录，不调用 `PUT /user/login-cache`。registry 重启不会自行撤销用户 token，最终有效性由中央决定。旧 `POST /knowledge-accumulation/registry/api/auth/login` 已移除，返回 404；开发固定凭据与钉钉 code 均应提交给知识后端的登录入口。中央请求与错误处理见[中央用户认证](../capability/infrastructure/middleware-user.md)。
