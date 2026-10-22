# 员工刷新与姓名待同步契约

> **用途：** 定义手动刷新使用的最小员工快照、数据库更新范围、SQLite 姓名标记与结果状态。流程与配置见[员工资料刷新](../../capability/application/personnel-refresh.md)，HTTP契约见[刷新接口](../../api/user.md#刷新已有员工资料)。

---

## 员工与部门更新

刷新目标为 `employees` 的 userid、name、department_id，范围在本次读取时确定。仅执行：

- 已有员工：更新 name、department_id，不改 userid、avatar_url、platform_permissions。
- 本次员工所属的顶级部门：新增缺失部门或更新 name，不删除其他部门。
- platform_admins：不写入。

SQL 使用绑定参数，在 MySQL 单一事务中对照加锁后的姓名/部门快照；员工被并发修改/删除时返回409，不能新建该员工。钉钉资料未全部通过校验时，不进入 MySQL 更新。

---

## SQLite 待同步表

`SQLitePersonnelSyncRepository` 在现有审核 SQLite 数据库中惰性创建辅助表 `personnel_name_sync`，不改变审核表或 user_version；默认仍随 SQLite 数据卷持久化。字段如下：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| userid | TEXT COLLATE BINARY | 主键，1—128字符，区分大小写，待同步姓名的已有员工标识。 |
| revision | TEXT | 每次登记生成新的32位随机十六进制修订标识；确认仅删除同一标识，删除后重新登记也不能复用旧修订。 |

不保存姓名载荷或用户/平台token。标记在 MySQL 更新前持久化；同步发生于 MySQL 提交后，始终读取 MySQL 当前姓名。上次远端失败或进程中断遗留的标记由下次手动刷新重试。发送成功但确认失败可以重复发送当前姓名；中间件增量更新不携带权限，不删除其他中央用户或登录缓存。

---

## 成功与失败结果

成功为 `PersonnelRefreshResult`：

| 字段 | 含义 |
| --- | --- |
| refreshed_users | 本次已完成钉钉读取与 MySQL 更新阶段的员工数，不表示每条记录都发生变化。 |
| refreshed_departments | 本次涉及的不同顶级部门数。 |
| synced_names | 本次已确认并移除标记的姓名同步数，包含历史待重试项。 |
| pending_names | 成功时为0。 |

失败 detail 包含 message、phase、database_updated、pending_names。phase为configuration、busy、fetch、prepare_sync、database或middleware_sync；database_updated为false表示未执行MySQL变更，true表示数据库阶段已完成，null表示数据库阶段提交结果不能确认。pending_names仅在已读取待同步数量时为整数，其余为null。错误不等于跨存储已回滚，具体恢复行为见[阶段说明](../../capability/application/personnel-refresh.md#失败与恢复)。
