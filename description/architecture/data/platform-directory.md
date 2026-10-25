# 平台目录内存契约

本契约描述注册中心缓存的公开平台信息及其时效元信息。数据来自中间件[查询所有平台接口](../../../../../知识沉淀中间件服务/middleware-service/description/api/platform.md#查询所有平台)，不落盘到 MySQL 或 SQLite。

---

## 平台对象

`app/model/platform.py` 的 `PlatformInfo` 是严格类型、不可变对象，只保留中间件的八个上游字段。字段缺失、类型错误、空白名称或描述、非法代号均拒绝；未知扩展字段不保留。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | string | 平台唯一代号，使用小写英文、数字及分隔用的连字符。 |
| `name` | string | 平台中文展示名称。 |
| `description` | string | 平台用途说明。 |
| `supports_a2a` | boolean | 配置声明的是否参与 A2A 协议。 |
| `is_sub_management_platform` | boolean | 配置声明的是否为子管理平台。 |
| `is_management_entry` | boolean | 配置声明的是否作为管理平台入口。 |
| `a2aresponsibility` | string | 平台在 A2A 协作中的职责，必填且允许空字符串，仅保存在内部目录，不在 registry 平台列表中返回。 |
| `is_alive` | boolean | 最近一次查询时，中间件内是否存在该平台的有效登录会话。 |

`a2aresponsibility` 严格接收字符串并保留原文，缺失、null 或其他类型导致整次刷新失败；子管理平台非空职责规则由中间件配置端负责。

三个能力与类型标记均为必填严格布尔值，原样保留；不参与用户或管理员权限判断。

完整列表按上游顺序保存，不允许重复 `code`。`is_alive` 不能接受字符串或数字代替布尔值，不表示业务健康、网络可达或消息服务健康。查询不会替其他平台续期。

内存快照使用 `PlatformDirectoryEntry`，在公开字段基础上增加本地属性：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `last_alive_at` | `datetime \| None` | 最近一次成功同步确认该平台存活的 UTC 时间；没有可用存活记录时为空。 |

每次成功同步且 `is_alive=True` 时，`last_alive_at` 更新为本次同步时间。同次响应中的存活平台使用相同时间。`is_alive=False` 时保留此前的值；首次出现便失活的平台为空。同步失败不改变此时间。

该时间由注册中心本地记录，不读取上游同名扩展字段；它是最近一次观测到存活的时间，不是平台自身的心跳时间或准确下线时间。它与目录快照的 `updated_at` 分别表示“最后确认存活”和“目录最后成功同步”。

---

## 本地快照

`PlatformDirectorySnapshot` 是不可变快照，不是中间件的 HTTP 响应对象。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `platforms` | `tuple[PlatformDirectoryEntry, ...]` | 最近一次成功查询的全部平台，包含存活与失活平台。 |
| `updated_at` | `datetime \| None` | 本地成功刷新时的 UTC 时间；从未获取成功时为空。 |
| `next_sync_at` | `datetime \| None` | 后台下一次计划同步或重试的 UTC 时间；正在同步、未启动、禁用或已停止且没有计划时为空。 |
| `is_stale` | boolean | 读取时，该快照是否尚未初始化、刷新失败、超过过期时间或服务已关闭。 |

`alive_platforms` 属性返回该快照中 `is_alive=True` 的平台；`get(code)` 返回对应平台，未知代号返回 `None`。两者仍须结合 `is_stale` 判断，不能把过期快照中的存活标记当成当前状态。

初始化时 `platforms=()`、`updated_at=None`、`next_sync_at=None`、`is_stale=True`。成功收到空数组时，`platforms=()`、更新时间有效、`is_stale=False`，与未获取到数据区分。

---

## 更新与时效

完整响应校验成功后一次替换全部对象，移除上游不再返回的平台及其本地存活记录；读取者持有的旧快照保持原值，不受后续刷新影响。过期判断使用单调时钟，UTC 更新时间只供展示和追溯，不受系统时间校准影响。

失败时保留上一次的平台列表和更新时间，并标记过期，不批量将平台改成失活。恢复成功后以新响应整体替换并清除过期标记。每个进程独立维护快照，重启后重新查询，没有持久化或跨进程共享。平台被移除后重新出现、或进程重启后，无法恢复此前的存活观测时间；再次同步确认存活后重新记录。

`next_sync_at` 属于读取时的调度元信息，不来自中间件响应。成功同步后按刷新周期登记，失败或等待令牌时按重试周期登记；开始同步和停机时清空。它表示计划而非保证完成的时间，不改变 `updated_at` 或 `last_alive_at` 的含义。

周期调度、配置、调用示例及启停行为见[平台目录刷新服务](../../capability/application/platform-directory.md)。
