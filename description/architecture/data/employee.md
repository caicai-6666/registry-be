# 公司部门与员工数据契约

> **用途：** 描述 MySQL 部门、员工与子平台管理员存储。首次钉钉初始化由知识后端负责，registry 读取同源资料并维护后续权限与管理员；已提供单表用户/部门列表及已有员工资料手动刷新，不全量导入钉钉通讯录；人员管理接口均要求当前注册中心管理员；独立原件入口的授权规则见[授权依赖](../../capability/application/authorization.md)。

---

## 部门表

`departments` 仅保存员工所属的公司顶级部门，不维护完整组织树。

| 字段 | 类型与约束 | 含义 |
| --- | --- | --- |
| `id` | `BIGINT UNSIGNED`，主键，不自增 | 钉钉部门 ID。 |
| `name` | `VARCHAR(200)`，必填，去除首尾空格后非空 | 顶级部门名称，不要求唯一。 |

首次登录逐级读取所属部门的父部门，保存企业根部门（ID 为 1）下的一级部门。多个所属部门归于同一顶级部门时合并；跨多个一级部门时全部保留，不把列表顺序或部门内人员排序当作主部门。仅属于企业根部门时保留根部门，根部门与实际部门同时出现时使用实际一级部门。

---

## 员工表

| 字段 | 类型与约束 | 含义 |
| --- | --- | --- |
| `userid` | `VARCHAR(128)`，主键，区分大小写，非空且不允许首尾空格 | 钉钉提供的用户唯一 ID，不另设自增用户 ID。 |
| `name` | `VARCHAR(200)`，历史允许 null，非空值不能全为空格 | 钉钉员工姓名，新初始化必须提供。 |
| `avatar_url` | `VARCHAR(64)`，ASCII，可为空 | 相对 data 的头像地址，格式为 `user/avatar/<32位随机标识>.webp`。 |
| `department_id` | `BIGINT UNSIGNED`，非空，外键关联 `departments.id` | 旧版兼容投影，不代表唯一或主要部门。 |
| `department_ids` | `JSON`，可空 | 全部一级部门 ID 的非空、去重数组；历史 NULL 回退为 `[department_id]`。 |
| `platform_permissions` | `JSON`，非空，默认 `{}` | 平台代码到文件访问权限等级的映射，不表示管理权限。 |
| `work_profile` | `JSON`，非空 | 任职资料，包含直属主管 userid 和职位，见下节。 |

`department_id` 具有查询索引；存在关联员工时，数据库禁止删除部门或修改部门 ID，避免产生孤立员工。员工转部门须同时更新完整 `department_ids` 和旧版兼容 `department_id`；数组成员由应用同事务维护，JSON成员不具有逐项外键。

---

## 任职资料与初始化

`employees.work_profile` 与知识后端保持同一字段契约，仅保存两个固定键：

```json
{"manager_userid":"manager-001","title":"研发工程师"}
```

`manager_userid` 为直属主管的企业 userid（最多128字符），不取部门负责人；`title` 为职位名称（最多200字符）。两者允许 null，去除首尾空白后为空的值归一为 null，拒绝非字符串、超长值或额外键。任职资料不进入用户列表、登录身份或中间件 user/update。

首次员工初始化由知识后端的 `EmployeeOnboardingService` 从钉钉详情采集并与员工、部门在同一事务写入；已有员工再次登录不补采。本项目通过[手动刷新](../../capability/application/personnel-refresh.md)更新已有员工，钉钉未维护或返回空值时写入 null，可清除旧主管/职位，不保留过期任职资料。

已有共享人员库缺少 `work_profile` 时，上线前需手工执行知识后端的[015 任职资料迁移](../../../../knowledge-accumulation-be/schema/015-employee-work-profile.sql)，为历史员工初始化两个 null。当前部署项目 `003-personnel.sql` 已为新空卷包含此字段和约束，新库不重复执行 `015`；已有卷不会重跑初始化，应用也不执行 DDL；缺列或字段非法时刷新失败，不静默跳过。数据库账号需具备 work_profile 的 SELECT/UPDATE 权限；初始化脚本不触发真实库迁移或批量刷新。

---

## 平台文件访问权限

```json
{
  "registry": 2,
  "contract-pheno": 1
}
```

数据库要求该字段为 JSON 对象，键遵循现有平台代码格式：小写英文字母开头，后续使用小写字母、数字及连接非空片段的连字符；值必须是非负整数，拒绝字符串、布尔值、小数、负数或嵌套对象。

文件访问等级的具体业务含义暂未定义，不将等级解释为管理员或审核员角色。空对象表示尚未分配任何平台权限；某个平台缺少键表示未获得该平台授权，不能因缺失而赋予默认权限。知识后端首次登录将可见平台目录（含离线平台）和自身平台等级初始化为 `0`，隐藏平台不擅自补键；再次登录不重置已有权限。本后端的管理员原件接口仍要求注册中心管理权；普通用户通过[文件权限下载接口](../../api/resource.md#按用户权限获取正式文件原件)按来源平台比较当前权限与文件等级，详见[授权依赖](../../capability/application/authorization.md)；新出现的平台不会自动改写已有员工记录。

JSON 中的平台代码暂不关联平台目录外键，允许先配置后同步；后续权限管理入口需要校验平台是否存在。权限直接保存在员工行中，不新增员工平台关联表。

---

## 子平台管理员表

`platform_admins` 保存各子平台的管理员列表，每行关联一名员工；同一平台可以关联多位管理员，同一员工也可以管理多个平台。

| 字段 | 类型与约束 | 含义 |
| --- | --- | --- |
| `platform_code` | `VARCHAR(128)`，ASCII，区分大小写，必填 | 子平台代码，格式与平台权限键一致。 |
| `employee_userid` | `VARCHAR(128)`，区分大小写，必填，外键关联 `employees.userid` | 管理员的钉钉用户 ID。 |

两列组成联合主键，禁止重复关联同一平台和员工；`employee_userid` 单独建索引，支持查询员工管理的平台。员工仍被关联时，数据库禁止删除员工或修改其 `userid`，需先解除管理员关联。

管理员列表通过同平台的多行记录表达，不把用户 ID 列表存成 JSON，从而使每个成员都具有真实外键约束。读取某个平台的管理员名单可以使用：

```sql
SELECT e.userid, e.department_id, e.platform_permissions
FROM platform_admins AS a
JOIN employees AS e ON e.userid = a.employee_userid
WHERE a.platform_code = 'contract-pheno'
ORDER BY e.userid;
```

平台没有关联行时，管理员列表为空。平台目录目前仅在内存维护，因此 `platform_code` 不设平台外键；[用户授权管理](../../capability/application/user-access.md)使用当前平台目录校验新增授权，已有隐藏授权可明确撤销。管理员关联与 `employees.platform_permissions` 独立保存，不自动补写权限等级；知识后端的完整身份可通过 `managed_platforms` 返回该员工关联的全部平台代码；registry `/auth/me` 仅返回中央 userid 和 username，员工仓库的管理目录映射仍提供全部关联代码，去重并排序。员工查询使用同一条 SQL 读取员工及管理员关联，查询失败按员工目录故障处理，不伪装为没有管理权限。知识后端统一通过中间件全量接口发布管理员关联，registry 启动时不再发布；[平台管理依赖](../../capability/application/authorization.md)通过中间件当前平台成员名单校验管理权，注册中心管理路由已接入该依赖，具体范围见依赖文档。

---

## 初始化与接入边界

基础建表定义位于 [003-personnel.sql](../../../../knowledge-accumulation-deploy/mysql/init/003-personnel.sql)，由 MySQL 容器在空数据卷首次初始化时执行，先建部门表，再建员工表。已有开发库需单独执行该脚本；重建镜像不会使已有数据卷重跑初始化，禁止通过清空卷补建表。

脚本使用 `CREATE TABLE IF NOT EXISTS`，可重复补建缺失表，但不会升级已经存在的同名表。部署前应检查实际表结构，不能将某个开发环境已完成的补列视为所有环境已就绪。首次登录事务会保存顶级部门及员工，姓名和头像地址允许 null 以兼容旧记录，已有员工再次登录不自动补写或覆盖资料。

子平台管理员表由 [004-platform-admins.sql](../../../../knowledge-accumulation-deploy/mysql/init/004-platform-admins.sql) 创建，必须在员工表之后执行。新部署按初始化文件名顺序建表，已有卷的补建方式见 [MySQL 镜像说明](../../../../knowledge-accumulation-deploy/mysql/readme.md#初始化与数据边界)。

钉钉登录、首次员工初始化与用户 token 签发由知识后端负责，registry 认证只查询中间件中央 token、用户名及管理员名单，不读取本地员工资料，不根据本地开发配置建立身份。登录与审核身份见[登录身份契约](authenticated-user.md)，已有审核记录中的姓名不会自动转换为钉钉 `userid`。运行与部署依赖见 [MySQL 基础设施](../../capability/infrastructure/mysql.md)。

MySQL 的 `avatar_url` 保存内部存储引用；登录和当前身份响应中的同名字段仅返回提取后的文件名，用于调用知识后端 `/knowledge-accumulation/api/resource/avatar/{filename}`。

头像文件存储、无损编码和失败清理见[员工头像迁移](../../capability/infrastructure/avatar.md)。已有旧版员工表不会因为重跑建表 SQL 自动新增字段；升级语句见[MySQL 镜像说明](../../../../knowledge-accumulation-deploy/mysql/readme.md#初始化与数据边界)。

平台列表的 `admins` 通过 `platform_admins` 与 `employees` 联结取得姓名与头像，返回对象列表（`userid`、`name`、`avatar_url`），历史姓名未知时使用 userid、无头像时为 null，头像只返回文件名；该展示不参与登录或管理权限判定，详见[平台 API](../../api/platform.md)。


员工姓名、部门和任职资料的手动更新由[员工资料刷新](../../capability/application/personnel-refresh.md)负责，使用单一事务保留权限、头像和管理员关系；中央姓名失败重试标记见[刷新辅助表](personnel-refresh.md#sqlite-待同步表)。


平台等级及管理员关系的事务变更与重试标记见[用户授权契约](user-access.md)，接口不修改员工姓名、部门或头像。


---

## 多部门字段

`department_ids` 为完整一级部门ID的非空、去重JSON数组，新增API列表字段同名。数据库NULL兼容历史单部门及旧写入，读取时使用 `[department_id]`。新写入按ID排序，旧 `department_id`为首项兼容投影，不是主要部门。部门名称仍来自部门表。更新流程与迁移见[员工刷新](../../capability/application/personnel-refresh.md#多一级部门归属)。
