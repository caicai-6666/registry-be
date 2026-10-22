# 用户文件权限与平台管理员变更

## 用途与接口

注册中心管理员通过[更新接口](../../api/user.md#更新用户权限与管理员关系)，指定已有员工 userid、平台文件等级变更和平台管理员变更。两类授权相互独立：管理员标志不由等级推导，修改等级也不自动添加管理员。

用户列表、部门列表和头像契约不变。本功能不创建员工、不修改姓名/部门/头像、不签发登录，不修改中间件实现或调用全表接口。

---

## 增量请求与平台范围

- platform_permissions：仅修改给出的平台键；非负整数设置等级，null移除该键。0是显式等级，不等于未授权。省略的平台保留。
- platform_admins：仅修改给出的平台；true添加、false移除，省略的平台保留。
- 至少有一个非空变更映射；等级拒绝布尔、字符串、负数，管理员标志只接受真正的布尔值，userid不得含首尾空白。

新增或设置授权的平台必须存在于当前未过期的平台目录，离线平台仍可管理。目录隐藏的平台不开放新授权；已有隐藏或已移出目录的平台，仍允许null撤销文件授权、false撤销已有管理员关系。目录不可用时返回503，不凭客户端平台代码写入数据库。

---

## 持久化与即时中间件同步

1. 查询目标员工当前权限和管理员关系；员工不存在返回404，不创建员工。查询使用两条单表SQL，不JOIN。
2. 校验目录与请求，生成实际变化集合，先保存SQLite待同步标记。
3. MySQL事务锁定员工行并核对权限快照；发生并发修改返回409。合并指定等级键、增删指定管理员行，最后提交。只修改employees.platform_permissions和platform_admins，不写其他资料。
4. 提交成功后立即处理该用户的待同步项。权限通过 `POST /middleware-service/api/user/update` 发送 `{userid, permissions}`；中间件此字段为整体替换，因此发送合并后的MySQL当前完整映射，不发送username。
5. 管理员通过 `POST /middleware-service/api/platform/admins/add` 或 `/remove`，精确发送 `{platform_code, userid}`。HTTP200且响应平台、用户一致才确认；changed=false表示已达到状态，同样算成功。
6. 所有待同步项确认后返回200及完整当前权限、管理平台列表。失败项不删除，返回503和明确的数据库提交状态；再次提交相同请求会重试，SQL不再变化也可处理历史标记。

平台会话使用registry自己的token，与用户请求Bearer分离；中间件401只失效本次使用的平台token。姓名刷新和权限同步分别只修改自身字段，避免覆盖彼此或其他平台用户资料。不调用用户/管理员full-table、PUT登录缓存或用户心跳。

---

## 失败恢复与并发边界

| 情况 | 行为 |
| --- | --- |
| 参数错误、未知平台 | 422，本地不更新。 |
| 员工不存在 | 404，本地不创建。 |
| MySQL快照冲突 | 409，回滚事务，刷新页面后重试。 |
| MySQL写入或提交异常 | 请求回滚；提交状态不确定时返回database_updated=null，不宣称未提交。 |
| 数据库已提交、部分中间件失败 | 503，database_updated=true，返回pending_updates；保留本地状态和同步标记。 |
| 完全重复且无待同步项 | 200，不重复发布中央变更。 |

SQLite标记只保存userid、操作类别、平台代码和随机修订标识；发送前重新读取MySQL状态，不重放旧请求的等级或管理员布尔载荷。仅成功的同一修订可以确认；进程重启后标记保留。当前重试入口为再次提交该用户的有效更新，不新增后台同步循环。

同实例更新串行并计入总超时（复用中间件请求超时配置）；跨实例通过MySQL行锁与快照冲突校验避免覆盖本地新授权。MySQL、SQLite和中央HTTP不是一个分布式事务，中央多项变更可能部分成功；接口不会将其报告为全部成功。其他后端的旧全表快照仍可能覆盖中央新值，需要统一人员源和外部切换/发布协调，进程锁不解决该问题。

允许明确撤销自身registry管理权，成功后后续请求返回403；若剩余项目同步失败，需其他仍有权的管理员重试。不会自动修改其他管理员来维持自己的访问权。

---

## 依赖与实现

复用现有MySQL、SQLite、平台会话和平台目录，无新凭据或开关。MySQL账号需要employees SELECT/UPDATE，以及platform_admins SELECT/INSERT/DELETE权限。SQLite辅助表定义见[权限变更契约](../../architecture/data/user-access.md)。

- `app/model/user_access.py`：严格请求、当前状态与合并规则。
- `app/repository/employee.py`：两类授权的单一事务。
- `app/repository/user_access_sync.py`：持久待同步标记。
- `app/service/user_access.py`：范围校验、事务编排、即时增量同步及重试。
- `app/infrastructure/middleware.py`：已有用户更新适配及新增管理员增删适配。
- `app/router/user.py`、`app/main.py`：路由与生命周期装配。

`tests/test_user_access.py`与`tests/test_user_access_api.py`覆盖严格类型、零等级/撤销、未指定平台保留、管理员独立变更、SQL回滚、提交后发送、未知平台、持久重试、超时/401和接口授权。所有HTTP/SQL均为隔离替身，SQLite在tests/tmp并清理，未执行真实权限变更。
