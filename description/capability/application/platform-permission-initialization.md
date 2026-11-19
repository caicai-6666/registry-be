# 新平台文件权限初始化

注册中心在取得完整中间件平台目录后，按稳定 `platform_code` 持久登记平台。首次上线快照只建立历史基线；此后首次发现的平台，为事务执行时的在职员工补齐缺失的 **0 级文件权限**。已有等级保留，离职员工不补，管理员关系完全不参与本流程。

---

## 执行与恢复

`PlatformDirectoryService.refresh` 校验目录成功且包含注册中心自身后，在公开新快照之前调用 `PlatformPermissionRepository.discover`。平台是否在线不影响身份；下线、重连、目录暂时移除后再出现、进程重启均不重新初始化同一个代码。空目录拒绝建立基线。

MySQL 单事务按以下顺序执行：

1. `SELECT baseline_ready FROM platform_permission_control WHERE id=1 FOR UPDATE`，取得跨后端共享锁。
2. 读取持久平台登记；首次完整目录只插入 baseline 平台并设置 baseline_ready，不改任何员工权限。
3. 后续发现新代码时，插入登记，锁定在职员工行；仅补齐缺键且无人工决策标记的项，等级固定为 0。
4. 对实际变更的员工写入同事务 outbox，再提交。写入失败整批回滚；不保留“登记成功但员工漏补”的半完成状态。

人工 `update-permissions` 也先取得同一 gate，然后锁员工。每个显式权限键都写入 `platform_permission_overrides`，包括对本已缺失键的撤销；无论设置还是撤销均写 outbox。重试不会按旧请求重新生成默认授权。自动初始化从不访问或修改 `platform_admins`。

`PlatformPermissionService` 独立于目录刷新持续消费 outbox，间隔复用 `MIDDLEWARE_RETRY_INTERVAL_SECONDS`。每名员工在 gate 内锁定读取最新 SQL 权限并发送中间件 `user/update`，只携带 userid 和完整 permissions 映射；回执确认后删除相同 revision。失败回滚、保留标记并继续其他员工，重启继续处理。员工已删除时清除孤立标记。人工即时发布同样使用该路径，不发送 gate 之前捕获的旧权限。

---

## 与知识后端协作

首次创建员工与平台发现必须遵循同一 gate 锁顺序。知识后端只在确认员工不存在后，合并当前平台目录与持久登记代码初始化 0 级，并尊重历史人工决策标记；员工已经存在时登录不得重新补键。同一事务创建 outbox。这样员工和平台无论谁先提交，后提交的事务都能观察到另一方。

知识后端的用户全表发布须在同一 gate 内重新读取权威员工表并持锁至 HTTP 回执；不能先读旧快照再等待 gate。全表发布不删除 registry outbox，重复发送最新权限可幂等恢复。首次建档、全表发布与 registry 必须作为配套版本上线。

gate 仅协调遵循协议的写入方，直接 SQL 修改者必须自行遵守。HTTP 超时/断连不证明中间件没有提交；目前中间件没有版本 fencing，服务端迟到请求仍可能覆盖较新中央快照。该机制保证 SQL 权威授权不会因重连或初始化重试复活，并约束正常回执下的跨后端发送顺序，不宣称跨系统原子事务或消除此远端不确定性。故障后应比较 SQL 与中央权限，必要时受控重启知识后端触发启动全表注册（会同时执行既有管理员全表，须确认权威数据范围），或按当前 SQL 权限重新提交人员权限更新；不得为“修复”而重新填充历史缺键。

---

## 迁移与上线

共享 MySQL 表由统一 deploy 维护，应用不自动执行 DDL。升级脚本为 [20261119-platform-file-permissions.sql](../../../../knowledge-accumulation-deploy/mysql/migrations/20261119-platform-file-permissions.sql)，空库由 deploy 初始化脚本创建。必须先安装以下表及 gate 初始行：

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| platform_permission_control | id=1，baseline_ready=false | 跨后端串行化与基线完成标记。 |
| platform_permission_registry | platform_code 主键，is_baseline，registered_at | 永久登记平台身份，不随离线删除。 |
| platform_permission_overrides | userid、platform_code 联合主键 | 显式人工权限决策，不随重试丢弃。 |
| platform_permission_outbox | userid 主键，revision 32 位随机十六进制 | 事务内持久待同步标记，不保存旧权限载荷。 |

userid 使用 `utf8mb4_0900_bin`，与 employees 一致；平台代码和 revision 使用 `ascii_bin`。不设级联删除外键，避免员工删除重建时丢失撤权标记。数据库账号需要上述表的 SELECT/INSERT/UPDATE/DELETE 权限；仍按单个 registry worker 运行。无需新增环境变量、Python 依赖或 SQLite 迁移。

上线步骤：暂停相关后端写入，备份并运行 deploy 的增量迁移（不得重跑空库初始化），保留已有权限；在首次建立基线前不要接入新平台；发布配套知识后端与 registry 后，等待目录成功刷新并核对 `baseline_ready=1`、现有平台均为 baseline，然后才接入新平台。MySQL 缺表或缺 gate 行时明确失败并后台重试，不把失败当作空基线。目录更新失败不会公布半初始化的新快照。

历史缺权不能区分漏初始化与主动撤权，必须人工确认。可对指定平台运行以下只读检查（替换示例代码）：

```sql
SELECT userid,name,employment_status
FROM employees
WHERE employment_status='employed'
  AND JSON_CONTAINS_PATH(platform_permissions,'one','$."project-management"')=0;
SELECT * FROM platform_permission_control;
SELECT * FROM platform_permission_registry ORDER BY platform_code;
SELECT userid,revision FROM platform_permission_outbox ORDER BY userid;
SELECT userid,platform_code FROM platform_permission_overrides ORDER BY userid,platform_code;
```

不要批量补历史名单。获确认后由管理员通过原人员权限接口逐项授权，避免绕过审计标记与 outbox。通过后台 outbox 完成与中间件回读核对判断同步结果，HTTP 健康检查只表示 Web 存活。

---

## 验证

本地 `tests/test_platform_permission.py` 使用隔离 SQLite 事务与可控共享锁验证 SQL 操作顺序和状态恢复，不连接真实业务数据库。覆盖首次基线不补、在职缺键补0、已有等级保留、离职跳过、零管理员新增、人工标记保护、重连和重启不复活、事务回滚重试、发布失败后读最新撤权状态、持锁发布及单人故障隔离。SQLite 适配测试不能替代真实 MySQL 跨进程锁和线上中间件验收。

配套实现为 `app/repository/platform_permission.py`、`app/service/platform_permission.py`、人员仓库权限事务及应用生命周期装配。上线前在隔离环境分别验证“先员工后平台”“先平台后员工”、人工撤权后重连、中央故障恢复，并核对用户不成为平台管理员。本轮不操作生产或真实员工权限。


`tests/test_platform_permission_mysql.py` 另在一次性 MySQL 8.4.13 容器、随机独立 schema 中验证实际 repository：先基线后新平台、人工撤权后失败重试及重建 repository、发布锁阻塞并发撤权、非法权限触发整批回滚，3 项全部通过。全部样本为合成数据，测试结束删除 schema 和容器；未连接业务库。知识后端配套提交 `10ca4a7`，deploy 表迁移提交 `f99fcac`；HTTP 超时后远端迟到提交未在本轮模拟中解决。
