# 用户授权变更与待同步标记

> **用途：** 定义用户平台等级/管理员变更的请求、当前状态和SQLite恢复标记。流程见[授权管理](../../capability/application/user-access.md)，HTTP见[更新接口](../../api/user.md#更新用户权限与管理员关系)。

---

## 请求与当前状态

`UserAccessPatch`字段：

| 字段 | 规则 |
| --- | --- |
| userid | 已登记员工的唯一标识，区分大小写，不接受空白或首尾空白。 |
| platform_permissions | 平台代码到非负整数或null的映射；null删除键，0保留为显式等级，未指定键不修改。 |
| platform_admins | 平台代码到严格布尔的映射；true新增、false删除，未指定平台不修改。 |

两个映射默认空对象，但不能同时为空；拒绝额外字段和顶层null。员工不存在时不通过该接口初始化。

成功返回 `UserAccessState`，仅含userid、完整platform_permissions和按代码排序的完整managed_platforms，不返回姓名、部门、头像或token。用户列表采用[人员管理 API](../../api/user.md#获取用户列表)的六字段契约，包含完整 `department_ids`；本接口当前状态用单表查询组合，不JOIN部门或员工展示资料。

---

## 本地写入边界

MySQL仍使用现有employees与platform_admins，不增表或改DDL。事务锁定员工并核对内部读取的权限快照；仅写权限JSON和指定平台的管理员关联。JSON生成与SQL参数绑定在服务端完成，不能把客户端字典作为SQL列名或条件。未指定平台和所有个人资料保持原值。

数据库提交后再发送中央请求。权限发送当前完整映射，符合中间件permissions整体替换语义；姓名和部门字段省略，保留中央当前资料。管理员增删以MySQL当前成员关系为准，不反复替换其他平台的管理员集合。

---

## SQLite辅助表 user_access_sync

由 `SQLiteUserAccessSyncRepository` 惰性创建，位于当前SQLite数据库（默认data/review-log/registry.db），不修改审核表或user_version。

| 字段 | 规则 |
| --- | --- |
| userid | TEXT COLLATE BINARY，1—128字符。 |
| kind | permissions或admin。 |
| platform_code | permissions任务为空字符串；admin任务为目标平台代码。 |
| revision | 每次登记新生成的32位随机修订标识，删除后重建也不复用旧标识。 |

联合主键为(userid,kind,platform_code)。只保存恢复标记，不保存授权等级、管理员布尔、姓名或凭据。相同用户权限任务会合并，管理员任务按平台合并；发送时读取当前MySQL状态。确认仅删除同一联合键和修订标识，不误删后来登记的任务。

---

## 部分失败结果

业务错误detail包含message、phase、database_updated、pending_updates：

- phase：validation、prepare_sync、database或middleware_sync。
- database_updated：false代表未提交本次变更；true代表本地事务阶段已完成；null代表数据库写入/提交结果不能确认。
- pending_updates：已统计时为待同步项数量，否则null。

数据库异常不能假定中央已回滚，HTTP超时也不能假定未提交。持久恢复和操作权限见[失败边界](../../capability/application/user-access.md#失败恢复与并发边界)。
