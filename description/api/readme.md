# API 参考

注册中心业务接口使用 `/knowledge-accumulation/registry/api` 前缀，业务数据与错误响应为 JSON，原件读取接口返回对应文件类型的二进制内容。接口机器 Schema 通过 `/openapi.json` 提供，交互文档位于 `/docs`。本文路径和各专题请求示例使用生产前缀；IDE 直接运行时使用 `http://127.0.0.1:16000/dev/knowledge-accumulation/registry/api`，Swagger 仍位于 `/docs`。运行方式见[环境配置](../capability/application/runtime.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取用户列表](user.md#获取用户列表) | `GET` | `/knowledge-accumulation/registry/api/user/user-list` |
| [获取部门列表](user.md#获取部门列表) | `GET` | `/knowledge-accumulation/registry/api/user/department-list` |
| [刷新已有员工资料](user.md#刷新已有员工资料) | `POST` | `/knowledge-accumulation/registry/api/user/refresh-all` |
| [更新用户权限与管理员关系](user.md#更新用户权限与管理员关系) | `POST` | `/knowledge-accumulation/registry/api/user/update-permissions` |
| [获取当前身份](auth.md#获取当前身份) | `GET` | `/knowledge-accumulation/registry/api/auth/me` |
| [查看子平台及存活状态](platform.md#查看子平台及存活状态) | `GET` | `/knowledge-accumulation/registry/api/platform/list` |
| [查看下次平台同步时间](platform.md#查看下次平台同步时间) | `GET` | `/knowledge-accumulation/registry/api/platform/sync-time` |
| [获取所有正式文件基本信息](catalog.md#获取所有正式文件基本信息) | `GET` | `/knowledge-accumulation/registry/api/catalog/list` |
| [调整正式文件等级](catalog.md#调整正式文件等级) | `POST` | `/knowledge-accumulation/registry/api/catalog/adjust-file-level` |
| [获取文件等级调整历史](catalog.md#获取文件等级调整历史) | `GET` | `/knowledge-accumulation/registry/api/catalog/file-level-history` |
| [代理读取用户头像](resource.md#代理读取用户头像) | `GET` | `/knowledge-accumulation/registry/api/resource/avatar/{filename}` |
| [按 ID 获取正式文件原件](resource.md#按-id-获取正式文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/file/{resource_id}` |
| [按审核 ID 获取待入库文件原件](resource.md#按审核-id-获取待入库文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/ingestion/{review_id}` |
| [按用户权限获取正式文件原件](resource.md#按用户权限获取正式文件原件) | `GET` | `/knowledge-accumulation/registry/api/resource/authorized-file/{resource_id}` |
| [可信后台代用户获取正式文件原件](resource.md#可信后台代用户获取正式文件原件) | `POST` | `/knowledge-accumulation/registry/api/resource/service-authorized-file/{resource_id}` |
| [获取待入库审核列表](ingestion-review.md#获取待入库审核列表) | `GET` | `/knowledge-accumulation/registry/api/ingestion-review/list` |
| [按审核员获取入库审核历史](ingestion-review.md#按审核员获取入库审核历史) | `GET` | `/knowledge-accumulation/registry/api/ingestion-review/history` |
| [确定入库最终审核结果](ingestion-review.md#确定入库最终审核结果) | `POST` | `/knowledge-accumulation/registry/api/ingestion-review/{review_id}/decision` |
| [获取待删除审核列表](deletion-review.md#获取待删除审核列表) | `GET` | `/knowledge-accumulation/registry/api/deletion-review/list` |
| [按审核员获取删除审核历史](deletion-review.md#按审核员获取删除审核历史) | `GET` | `/knowledge-accumulation/registry/api/deletion-review/history` |
| [确定删除最终审核结果](deletion-review.md#确定删除最终审核结果) | `POST` | `/knowledge-accumulation/registry/api/deletion-review/{review_id}/decision` |

---

## 认证入口

| 调用场景 | 凭据与授权 |
| --- | --- |
| registry 管理 | 用户 Bearer token，中央登录有效且具备 registry 管理权。 |
| 用户正式原件 | 用户 Bearer token，按来源平台实时文件等级授权，不要求管理员。 |
| 可信后台正式原件 | knowledge-accumulation 平台 Bearer token 与可信任务绑定 userid，按员工实时文件等级授权。 |

---

## 接入顺序

先在知识后端登录取得用户 `access_token`；registry 直接向中间件验证该 token 并续期，不调用知识后端 verify。用户入口中，除[按用户权限获取正式文件原件](resource.md#按用户权限获取正式文件原件)外，管理接口（含当前身份、平台列表和管理员原件入口）均要求中央登录有效且具备注册中心（默认 `registry`）管理员资格。文件等级、其他平台管理权或本地展示字段均不能替代。头像文件通过知识后端 `/knowledge-accumulation/api/resource/avatar/{filename}` 获取。管理权限不足返回 403，权限查询不可用返回 503。通过待审列表取得审核 ID 后，可读取原件并提交最终决定；批准后使用返回的正式资源 ID 下载原件。删除审核通过申请关联的 resource_id 读取正式原件；批准后移除正式资源并保留原件，审核历史继续从 SQLite 查询。

中间件平台会话是独立的服务器间认证流程，不使用用户 token，参见[中间件注册与心跳](../capability/infrastructure/middleware-session.md)。

可信知识后端后台任务使用独立的[服务间原件入口](resource.md#可信后台代用户获取正式文件原件)，通过平台Bearer认证后代任务所属员工按实时文件权限读取，不要求用户登录；不能使用裸userid下载。
