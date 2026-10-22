# 人员管理 API

提供注册中心管理所需的人员与部门查询。统一前缀为 `/knowledge-accumulation/registry/api/user`，返回 JSON；授权规则见[管理员授权](../capability/application/authorization.md)。

## 接口目录

| 接口 | 方法 | 完整路径 |
| --- | --- | --- |
| [获取用户列表](#获取用户列表) | `GET` | `/knowledge-accumulation/registry/api/user/user-list` |
| [获取部门列表](#获取部门列表) | `GET` | `/knowledge-accumulation/registry/api/user/department-list` |
| [刷新已有员工资料](#刷新已有员工资料) | `POST` | `/knowledge-accumulation/registry/api/user/refresh-all` |
| [更新用户权限与管理员关系](#更新用户权限与管理员关系) | `POST` | `/knowledge-accumulation/registry/api/user/update-permissions` |

---

## 获取用户列表

`GET /knowledge-accumulation/registry/api/user/user-list`，按 userid 排序返回 MySQL employees 全部已登记用户。

**认证方式：** header `Authorization: Bearer <access_token>`，要求中央登录有效且为 registry 管理员。

**请求参数：** 无 path、query 参数或请求体，不分页、不按登录状态过滤。

**成功响应：** `200 application/json`，携带 `Cache-Control: no-store`：

```json
[{"userid":"employee-001","name":"张三","avatar_url":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp","department_id":1,"platform_permissions":{"registry":0}}]
```

每项固定五个字段：

| 字段 | 类型与含义 |
| --- | --- |
| `userid` | 员工唯一字符串标识，区分大小写。 |
| `name` | 员工姓名，未补全为 null；沿用数据库字段名，不改为 username。 |
| `avatar_url` | 头像文件名，无头像为 null；读取使用知识后端头像接口。 |
| `department_id` | 顶级部门 ID，前端与部门列表自行关联。 |
| `platform_permissions` | 平台文件访问等级对象；缺少平台键表示未授权，显式 0 保持原值，不表示管理权。 |

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 用户 token 缺失、无效或已过期。 |
| `403` | 登录有效，但不是 registry 管理员。 |
| `503` | 认证、管理员校验、人员仓库、数据库或员工数据暂不可用。 |

**请求示例：**

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/user/user-list' \
  -H 'Authorization: Bearer replace-with-user-access-token'
```

**行为与边界：** 只执行 employees 单表查询，不 JOIN、不查询部门或管理员关系，不返回 department_name、managed_platforms、登录状态或 token。头像沿用已有约定，将数据库相对路径转换为文件名；JSON 权限解析为对象。空表返回 []，数据库或数据解析故障返回脱敏 503，不伪装为空表。不请求钉钉或中间件总表，不包含只存在于知识后端开发配置中的模拟身份。复用员工仓库与现有 MySQL 配置，具体字段见[人员存储契约](../architecture/data/employee.md)，实现为 `MySQLEmployeeRepository.list_employee_records()`。

---

## 获取部门列表

`GET /knowledge-accumulation/registry/api/user/department-list`，返回 MySQL 已登记部门的 ID 和名称，按部门 ID 升序排列。

**认证方式：** header `Authorization: Bearer <access_token>`。先验证中央用户登录，再验证 registry 管理权限。

**请求参数：** 无 path、query 参数或请求体。

**成功响应：** `200 application/json`，携带 `Cache-Control: no-store`：

```json
[{"id":1,"name":"综合管理部"},{"id":5,"name":"研发部"}]
```

每项仅含 `id`（非负整数，钉钉顶级部门 ID）和 `name`（非空部门名称，最多 200 字符）。无部门时返回 `[]`。

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 用户 token 缺失、无效或已过期。 |
| `403` | 登录有效，但不是 registry 管理员。 |
| `503` | 中央认证、管理员校验、人员仓库、数据库或部门记录暂不可用。 |

**请求示例：**

```bash
curl 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/user/department-list' \
  -H 'Authorization: Bearer replace-with-user-access-token'
```

**行为与边界：** 直接查询同源 MySQL `departments`，包含暂未关联员工的部门；只返回当前已登记数据，不主动请求钉钉或初始化部门，不改写人员、权限及中间件资料。仓库由应用生命周期统一创建和关闭，复用现有 MySQL 配置，字段来源见[部门与员工契约](../architecture/data/employee.md)。数据库失败返回脱敏 503，不伪装为空列表。实现位于 `app/router/user.py`、`MySQLEmployeeRepository.list_departments()`，不新增独立后台同步任务。


---

## 刷新已有员工资料

`POST /knowledge-accumulation/registry/api/user/refresh-all`，一次刷新 MySQL 已有员工的钉钉姓名、顶级部门归属和部门名称，随后仅增量同步发生变化的姓名。

**认证方式：** header `Authorization: Bearer <access_token>`，要求中央登录有效且具有 registry 管理权限。钉钉应用凭据只从后端配置读取，不能由调用方传入。

**请求参数：** 无 path、query 参数或请求体。额外 query/body 返回422。请求同步等待流程完成，不创建后台作业。

**成功响应：** `200 application/json`，`Cache-Control: no-store`：

```json
{"refreshed_users":12,"refreshed_departments":3,"synced_names":2,"pending_names":0}
```

refreshed_users是本次处理范围，refreshed_departments是不同顶级部门数，synced_names是本次确认的姓名同步数（可包含历史失败项），不等于所有员工都发生了修改。无员工时返回各项为0，不导入其他钉钉员工。

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 未登录或用户 token 失效。 |
| `403` | 非 registry 管理员。 |
| `409` | 同实例已有刷新执行，或员工姓名/部门在资料读取期间发生并发修改。 |
| `422` | 传入了 query 或请求体。 |
| `503` | 缺少应用配置、钉钉/数据库/中间件不可用、部门无法确定、超时或姓名同步尚未完成。 |

业务503示例：

```json
{"detail":{"message":"数据库已更新，部分姓名尚未同步；再次刷新会重试","phase":"middleware_sync","database_updated":true,"pending_names":1}}
```

完整失败字段见[刷新结果契约](../architecture/data/personnel-refresh.md#成功与失败结果)。database_updated为false表示未执行MySQL变更，true表示数据库阶段已完成，null表示提交结果无法确认；不要将503统一当作数据库未更新。认证层错误沿用通用401/403/503结构。

**请求示例：**

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/user/refresh-all' \
  -H 'Authorization: Bearer replace-with-user-access-token'
```

**行为与边界：** 所有员工资料通过校验后才提交单一MySQL事务，保留头像、权限和管理员关系；遇到无法读取的员工不自动删除。只有姓名变化及历史待同步项调用中间件 user/update，body精确为userid、username，不携带permissions。不调用任何full-table或登录写入接口。只变更部门时不更新中间件；失败姓名标记持久保存，下次手动刷新读取当前姓名重试。配置、部门归属、并发和恢复说明见[员工资料刷新](../capability/application/personnel-refresh.md)。


---

## 更新用户权限与管理员关系

`POST /knowledge-accumulation/registry/api/user/update-permissions`，指定员工userid，增量更新平台文件等级与管理员关系。

**认证方式：** header `Authorization: Bearer <access_token>`；要求中央登录有效且具有registry管理权限，不能通过body伪造操作人。

**请求参数：** 无path或业务query参数；JSON body：

```json
{"userid":"employee-001","platform_permissions":{"contract-pheno":3,"registry":0,"old-platform":null},"platform_admins":{"contract-pheno":true,"registry":false}}
```

userid必填。platform_permissions和platform_admins至少一个非空；未指定平台不改变。等级必须是真正的非负整数，null撤销该平台键，0是有效等级；管理员标志只接受true/false，分别添加/移除。两类权限独立，管理员操作不会自动改变文件等级。平台范围、隐藏平台撤销与严格校验见[请求规则](../capability/application/user-access.md#增量请求与平台范围)。

**成功响应：** `200 application/json`，携带 `Cache-Control: no-store`：

```json
{"userid":"employee-001","platform_permissions":{"contract-pheno":3,"registry":0},"managed_platforms":["contract-pheno"]}
```

仅为示例；返回的是完整当前映射/管理列表，原有其他平台仍保留。200表示本地事务完成，且该用户所有待同步项均已确认。

**错误响应：**

| 状态码 | 条件 |
| --- | --- |
| `401` | 用户未登录、token无效或已过期。 |
| `403` | 当前用户无registry管理权限。 |
| `404` | 目标员工未登记，不创建新员工。 |
| `409` | 权限快照在更新期间被并发修改，已请求回滚，重读后重试。 |
| `422` | 请求为空、字段/类型非法，或平台不在当前可管理目录。 |
| `503` | 平台目录、数据库或中间件不可用，或中央变更未全部确认。 |

部分失败示例：

```json
{"detail":{"message":"数据库已更新，部分权限尚未同步；重复提交可重试","phase":"middleware_sync","database_updated":true,"pending_updates":1}}
```

失败字段见[授权变更契约](../architecture/data/user-access.md#部分失败结果)；database_updated为null时不能确认本地提交结果，不能将所有503当成数据库未更新。

**请求示例：**

```bash
curl --request POST 'http://127.0.0.1:23000/knowledge-accumulation/registry/api/user/update-permissions' \
  -H 'Authorization: Bearer replace-with-user-access-token' \
  -H 'Content-Type: application/json' \
  --data '{"userid":"employee-001","platform_permissions":{"contract-pheno":3},"platform_admins":{"contract-pheno":true}}'
```

**行为与边界：** 先提交MySQL，再即时增量请求中间件user/update、platform/admins/add或remove。权限发送合并后的完整映射，管理员仅逐平台增删；不调用任何full-table或登录写入。重复提交可重试持久待同步项，已确认项不重复发送。保留姓名、头像、部门、未指定平台。允许修改当前用户自身管理权，撤销registry管理权后后续请求将403；部分失败需其他管理员处理。具体事务、重试与跨后端边界见[用户授权管理](../capability/application/user-access.md)。
