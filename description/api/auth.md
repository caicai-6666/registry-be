# 人员登录 API

仅凭审核人员密钥签发免登码。人员文件见[人员定义](../architecture/data/reviewer-user-definition.md)，缓存与配置见[人员系统](../capability/application/personnel.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [使用密钥登录](#使用密钥登录) | `POST` | `/registry/api/auth/login` |

---

## 使用密钥登录

`POST /registry/api/auth/login`，仅凭密钥识别审核人并签发免登码。

### 认证方式

本接口不要求 Bearer 认证，凭据在 JSON 请求体中提交。

### 请求参数

媒体类型为 `application/json`，没有 path 或 query 参数。

| 位置 | 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- | --- |
| body | `secret_key` | 字符串 | 是 | 非空，与人员配置精确匹配；请求禁止额外字段。 |

### 成功响应

`200 OK`，媒体类型 `application/json`：

```json
{
  "login_code": "随机免登码",
  "user_name": "注册中心审核员"
}
```

`user_name` 来自服务端人员快照；`login_code` 为当前进程签发的凭据。

### 错误响应

| 状态码 | 触发条件 | 响应 |
| --- | --- | --- |
| `401` | 密钥未匹配人员，包括全空白密钥 | `{"detail":"审核用户密钥无效"}` |
| `422` | 缺少字段、密钥为空或类型错误、额外字段、非法 JSON | `detail` 错误列表，仅包含 `type`、`loc`、`msg`，不回显输入。 |

### 请求示例

替换本地 URL 和密钥；直接文件启动默认端口为 `23000`，容器内部为 `8000`。

```bash
curl -X POST http://127.0.0.1:23000/registry/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"secret_key":"替换为本地人员密钥"}'
```

### 行为与边界

免登码默认有效期 `3600` 秒，可通过 `AUTH_LOGIN_CODE_TTL_SECONDS` 修改。后续受保护路由需要 `Authorization: Bearer <login_code>`；成功解析后刷新有效期，已过期的码不会复活。认证依赖对缺失、格式错误、无效或过期凭据返回 `401`，并设置 `WWW-Authenticate: Bearer`。

重复登录不撤销旧码；应用重启后旧码全部失效。缓存不跨进程共享，当前没有登出、撤销或独立续期接口。[入库审核](ingestion-review.md)与[删除审核](deletion-review.md)均使用该身份确定最终审核人；OpenAPI 与 Swagger 入口仍可直接访问。
