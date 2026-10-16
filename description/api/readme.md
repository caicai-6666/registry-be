# API 参考

注册中心业务接口使用 `/registry/api` 前缀，业务数据与错误响应为 JSON，原件读取接口返回对应文件类型的二进制内容。接口机器 Schema 通过 `/openapi.json` 提供，交互文档位于 `/docs`。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [使用密钥登录](auth.md#使用密钥登录) | `POST` | `/registry/api/auth/login` |
| [查看子平台及存活状态](platform.md#查看子平台及存活状态) | `GET` | `/registry/api/platform/list` |
| [查看下次平台同步时间](platform.md#查看下次平台同步时间) | `GET` | `/registry/api/platform/sync-time` |
| [获取所有正式文件基本信息](catalog.md#获取所有正式文件基本信息) | `GET` | `/registry/api/catalog/list` |
| [按 ID 获取正式文件原件](resource.md#按-id-获取正式文件原件) | `GET` | `/registry/api/resource/file/{resource_id}` |
| [按审核 ID 获取待入库文件原件](resource.md#按审核-id-获取待入库文件原件) | `GET` | `/registry/api/resource/ingestion/{review_id}` |
| [获取待入库审核列表](ingestion-review.md#获取待入库审核列表) | `GET` | `/registry/api/ingestion-review/list` |
| [按审核员获取入库审核历史](ingestion-review.md#按审核员获取入库审核历史) | `GET` | `/registry/api/ingestion-review/history` |
| [确定入库最终审核结果](ingestion-review.md#确定入库最终审核结果) | `POST` | `/registry/api/ingestion-review/{review_id}/decision` |
| [获取待删除审核列表](deletion-review.md#获取待删除审核列表) | `GET` | `/registry/api/deletion-review/list` |
| [按审核员获取删除审核历史](deletion-review.md#按审核员获取删除审核历史) | `GET` | `/registry/api/deletion-review/history` |
| [确定删除最终审核结果](deletion-review.md#确定删除最终审核结果) | `POST` | `/registry/api/deletion-review/{review_id}/decision` |

---

## 接入顺序

使用本项目独立配置的人员密钥登录，取得 `login_code` 与显示名称。平台查看、文件查询、原件读取、入库审核和删除审核接口使用 Bearer 免登码。通过待审列表取得审核 ID 后，可读取原件并提交最终决定；批准后使用返回的正式资源 ID 下载原件。删除审核通过申请关联的 resource_id 读取正式原件；批准后移除正式资源并保留原件，审核历史继续从 SQLite 查询。

中间件平台会话是独立的服务器间认证流程，不使用人员免登码，参见[中间件注册与心跳](../capability/infrastructure/middleware-session.md)。
