# 员工刷新与资料、状态待同步契约

> **用途：** 定义手动刷新使用的最小员工快照、数据库更新范围、SQLite 资料及状态同步标记与结果状态。流程与配置见[员工资料刷新](../../capability/application/personnel-refresh.md)，HTTP契约见[刷新接口](../../api/user.md#刷新已有员工资料)。

---

## 员工与部门更新

刷新目标为 `employees` 的 userid、name、department_id、department_ids、work_profile、employment_status，范围在本次读取时确定。仅执行：

- 钉钉可读取员工：更新 name、department_id、department_ids、work_profile，并设置 employment_status=employed。
- 钉钉明确不存在员工：只设置 employment_status=resigned，保留原资料。
- 两类更新都不改 userid、avatar_url、platform_permissions。
- 本次员工所属的顶级部门：新增缺失部门或更新 name，不删除其他部门。
- platform_admins：不写入。

SQL 使用绑定参数，在 MySQL 单一事务中对照加锁后的姓名、部门、任职资料及在职状态快照；员工被并发修改/删除时返回409，不能新建该员工。钉钉明确不存在的员工跳过资料采集，与其余员工的更新一起提交状态；其他读取或校验失败时，不进入 MySQL 更新。全部不存在时仍提交离职状态。

---

## SQLite 待同步表

`SQLitePersonnelSyncRepository` 在现有审核 SQLite 数据库中惰性创建辅助表 `personnel_name_sync`，不改变审核表或 user_version；默认仍随 SQLite 数据卷持久化。字段如下：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| userid | TEXT COLLATE BINARY | 主键，1—128字符，区分大小写，待同步资料及状态的已有员工标识。 |
| revision | TEXT | 每次登记生成新的32位随机十六进制修订标识；确认仅删除同一标识，删除后重新登记也不能复用旧修订。 |

不保存资料或状态载荷，也不保存用户/平台token。全部目标（包括钉钉明确不存在的员工）在 MySQL 更新前登记标记；提交后始终读取 MySQL 当前资料和在职状态。在职发送姓名、全部部门名称和 employed；离职只发送 resigned，避免旧资料阻塞状态同步。数据库中已不存在的用户可移除标记，其余只有中央成功确认请求值后才按 revision 移除。中间件缺少或返回不一致状态时保留任务，不能用默认在职值代替确认。历史失败或进程中断由下次手动刷新重试；可能重复发送当前值，不重放旧状态，也不修改权限或登录缓存。

---

## 成功与失败结果

成功为 `PersonnelRefreshResult`：

| 字段 | 含义 |
| --- | --- |
| skipped_users | 本次钉钉明确返回不存在、跳过资料采集且已标记离职的员工数，默认0；不表示员工已删除或撤权。 |
| refreshed_users | 本次已完成钉钉读取与 MySQL 更新阶段的员工数，不表示每条记录都发生变化。 |
| refreshed_departments | 本次涉及的不同顶级部门数。 |
| synced_names | 本次已确认并移除标记的资料或状态同步员工数（含仅同步离职状态），包含历史待重试项。 |
| pending_names | 成功时为0。 |

失败 detail 包含 message、phase、database_updated、pending_names。phase为configuration、busy、fetch、prepare_sync、database或middleware_sync；database_updated为false表示未执行MySQL变更，true表示数据库阶段已完成，null表示数据库阶段提交结果不能确认。pending_names仅在已读取待同步数量时为整数，其余为null。错误不等于跨存储已回滚，具体恢复行为见[阶段说明](../../capability/application/personnel-refresh.md#失败与恢复)。


---

## 多部门变更快照

`EmployeeRefreshTarget.department_ids`保留读取时的完整JSON值（历史NULL保留为None）以检测并发变化；`EmployeeRefreshChange.department_ids`携带本次归并后的所有一级部门，`department_id`为排序首项兼容投影。多一级部门属于有效归属，不再作为错误；同事务保存，部门字段不参与权限提升。
