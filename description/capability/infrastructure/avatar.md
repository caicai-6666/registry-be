# 员工头像代理与存储边界

> **职责：** 头像初始化、无损 WebP 转码和磁盘存储继续由 knowledge-accumulation-be 负责。registry 提供受管理员认证保护的 HTTP 读取代理，不恢复本地头像目录或存储服务。

---

## 读取入口

registry 入口为 `GET /knowledge-accumulation/registry/api/resource/avatar/{filename}`，使用当前用户 Bearer token，先完成本平台中央登录及 registry 管理权限验证，再请求知识后端：

```text
GET /knowledge-accumulation/api/resource/avatar/{filename}
Authorization: Bearer <同一次请求的用户 access_token>
```

registry 不使用平台 token 代替用户 token，不调用登录写入、不缓存或下载落盘。知识后端再次验证该 token 并按既有规则续期；查询期间 token 若失效，上游 401 仍会使本次代理返回 401。

文件名限定为 32 位小写十六进制加 .webp。目标服务来自受控配置，路径固定拼接，客户端不能指定代理地址；不透传调用方的 query、Cookie 或任意请求头，也不复用上游 Cookie。关闭重定向和环境代理，防止用户凭据随跳转外发。

registry 接口契约见[代理读取用户头像](../../api/resource.md#代理读取用户头像)。知识后端直接接口只要求普通用户登录；其安全读取、目录及转码规则仍见[知识后端头像 API](../../../../knowledge-accumulation-be/description/api/resource.md)。

---

## 配置与生命周期

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `KNOWLEDGE_ACCUMULATION_BASE_URL` | 空 | 含端口的知识后端服务根地址，例如 `http://127.0.0.1:24000`；不包含接口路径、凭据、query 或 fragment。未填写时代理返回 503。 |
| `KNOWLEDGE_ACCUMULATION_TIMEOUT_SECONDS` | `10` | 包含连接和完整响应读取的总超时秒数。 |
| `KNOWLEDGE_ACCUMULATION_AVATAR_MAX_BYTES` | `10485760` | 代理解码后头像字节上限，默认 10 MiB。 |
| `KNOWLEDGE_ACCUMULATION_AVATAR_CACHE_SECONDS` | `86400` | 浏览器私有缓存秒数，必须为非负整数；0 时成功响应也禁止缓存。 |

环境变量优先于项目 .env，读取时不插值。容器部署须填写容器可达的主机/服务名和端口，不能将容器内 127.0.0.1 当作宿主机或另一个容器。路径由后端固定拼接，配置变化后重启服务，新策略对之后取得的响应生效；已有浏览器缓存仍按原有效期处理。

配置由 `app/config/knowledge.py` 维护；`app/main.py` 在地址存在时创建唯一 `KnowledgeClient`，由应用生命周期关闭。未配置不会阻止其他接口启动。读取采用受限流式缓冲，只接受非空 image/webp，超过上限、超时、重定向、非法媒体类型或上游故障返回脱敏 503。401、403、404、422保留语义，错误正文不原样转发；成功默认返回原始字节、`Cache-Control: private, max-age=86400`、`Vary: Authorization` 与 nosniff；错误保持 no-store。缓存时长由 `KNOWLEDGE_ACCUMULATION_AVATAR_CACHE_SECONDS` 决定，默认一天；正数作为 max-age，按 token 隔离，禁止公共代理缓存；0 时返回 no-store 且不设置 Vary。头像更新使用新随机文件名，因此新头像会使用新缓存地址。缓存命中不会请求后端或触发中央用户续期。

---

## 头像数据位置

知识后端使用自己的 AVATAR_ROOT，默认 data/user/avatar。registry 不装配 AvatarStore，也不读取旧 AVATAR_* 配置；新代理只通过网络获取头像。用户列表和平台管理员的 avatar_url 仍是文件名。

原有一份 WebP 已按相同文件名迁入知识后端，独立读取校验字节数与 SHA-256 一致后，来源文件及空目录已删除。此次增加代理不再次复制、移动或创建头像，不改知识后端配置及实际数据。知识后端容器的数据卷仍需单独持久化，本地迁移不代表容器卷自动完成迁移。

---

## 验证边界

`tests/test_avatar_proxy.py` 使用 HTTP 替身覆盖固定地址和端口、精确用户 Bearer、Cookie 隔离、重定向拒绝、媒体类型、空响应、响应上限、网络超时、状态码、路径、配置和关闭连接。`tests/test_resource_api.py` 验证未配置时 503 且无本地头像存储；`tests/test_management_dependencies.py` 将代理纳入统一管理员边界。

测试不连接真实知识后端，不调用共享服务，不读取真实头像；测试临时目录均位于 tests/tmp 并清理。
