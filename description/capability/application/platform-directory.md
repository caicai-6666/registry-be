# 平台目录刷新服务

`PlatformDirectoryService` 随 FastAPI 启动，定期调用中间件 `GET /middleware-service/api/platform/list`，在内存中维护全部平台信息和会话存活状态，供后续审核与管理能力读取。

> **存活边界：** `is_alive` 表示中间件中的登录会话仍有效。平台查询不替其他平台发送心跳，不验证业务健康；刷新失败也不等于所有平台失活。

---

## 职责与依赖

| 模块 | 职责 |
| --- | --- |
| `app/config/platform_directory.py` | 独立的刷新开关、周期、重试和快照过期配置。 |
| `app/model/platform.py` | 不可变公开平台对象与本地快照。 |
| `app/infrastructure/middleware.py` | GET 请求、Bearer 认证、HTTP 状态与完整列表校验。 |
| `app/service/platform_directory.py` | 周期刷新、原子替换、过期标记和故障恢复。 |
| `app/main.py` | 创建服务、注册生命周期，公开应用内读取入口。 |

认证复用[中间件注册与心跳服务](../infrastructure/middleware-session.md)。平台目录拥有独立 HTTP 客户端，请求超时沿用 `MIDDLEWARE_REQUEST_TIMEOUT_SECONDS`；关闭时先取消并等待查询，再释放客户端。应用在关闭会话服务之前关闭目录服务。

平台目录与入库消费开关独立，即使 `INGESTION_ENABLED=false` 仍可刷新平台状态。中间件地址或密钥未配置，或者目录开关关闭时，不启动刷新任务。启动只派发后台任务，不等待首次查询完成；中间件故障不会阻塞 Web 启动。

---

## 应用内读取

```python
service = request.app.state.platform_directory_service
snapshot = service.get_snapshot()

all_platforms = snapshot.platforms
alive_platforms = snapshot.alive_platforms
platform = snapshot.get("contract-pheno")
last_alive_at = platform.last_alive_at if platform else None
next_sync_at = snapshot.next_sync_at

if snapshot.is_stale:
    # 当前状态未知；允许展示上次记录和 updated_at，但不能据此判定当前存活。
    pass
```

平台对象的 `last_alive_at` 表示最近一次同步确认存活的 UTC 时间；失活时保留历史值，没有可用存活记录时为 `None`。目录快照的 `updated_at` 则在每次成功同步时更新；`next_sync_at` 是当前后台登记的下一次计划同步或重试时间，正在同步或没有计划时为 `None`。

`get_snapshot()` 是同步读取，无网络请求，无需 `await`。对象字段、空快照与时效规则的权威定义见[平台目录内存契约](../../architecture/data/platform-directory.md)。面向前端的[平台查看 API](../../api/platform.md)已接入此快照，提供全部非自身平台及各自存活状态、下次计划同步时间，复用人员认证，具体返回与过期行为由 API 文档维护。

`await service.refresh()` 可手工刷新并取得新快照；它与周期刷新共用锁，避免并发响应覆盖彼此。手工刷新需要有效会话，失败抛出异常，原数据仍保留并标为过期。

---

## 刷新与恢复

1. 等待注册会话提供有效令牌；未就绪时按重试周期等待，不发送查询。
2. 使用本次令牌发出无请求体、无查询参数的 GET 请求。
3. 要求 HTTP `200`，逐项校验四个公开字段及平台代号唯一性；完整成功才替换快照。
4. 成功后登记下次同步时间并等待刷新周期；空数组也是成功，会清空上次列表。
5. 网络、超时或响应校验失败时保留旧快照、标记过期，并登记重试时间，按重试周期再次查询。
6. 返回 `401` 时只失效本次请求使用的令牌，等待注册会话重新登录；迟到的 `401` 不会清除新令牌。

开始同步时清除旧计划；无令牌时登记下一次检查会话的重试时间。计划时间随实际后台等待更新，不使用上次成功时间简单推算。手工刷新不改变已登记的周期，未启动或已禁用的服务不产生计划。关闭时清空计划，读取旧快照始终标为过期，不能再次启动或刷新。日志只输出异常类型与 HTTP 状态，不记录令牌、密钥或响应原文。

---

## 配置

配置独立于中间件认证、入库消费和模型配置；进程环境优先于 `.env`，修改后重启。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `PLATFORM_DIRECTORY_ENABLED` | `true` | 启用周期平台查询。 |
| `PLATFORM_DIRECTORY_REFRESH_INTERVAL_SECONDS` | `30` | 成功刷新后的等待秒数。 |
| `PLATFORM_DIRECTORY_RETRY_INTERVAL_SECONDS` | `5` | 刷新失败或令牌尚未就绪时的等待秒数。 |
| `PLATFORM_DIRECTORY_STALE_AFTER_SECONDS` | `90` | 距离最近一次成功查询达到该秒数时，快照视为过期。 |

时间值必须为有限正数。失败立即标为过期，即使还未达到过期时长；读取时也会独立检查时长，避免后台任务未及时运行时将旧记录当作当前状态。

周期是本地查询频率，与登录续期心跳分别维护；存活变化只能在下一次成功查询时反映，不能承诺实时状态。快照仅在内存中，不写业务数据库或 `data/`。

---

## 验证

```bash
python -m unittest tests.test_platform_directory tests.test_middleware_session tests.test_ingestion_lifespan
```

测试使用隔离 HTTP，覆盖请求契约、不可变快照、平台新增与移除、存活变化及上次存活时间保留、空数组、错误响应、过期时限、401 及令牌更新、超时、停机取消、开关和 FastAPI 非阻塞启动。生命周期测试数据只保存在 `tests/tmp`，结束后清理。
