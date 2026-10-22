# 已有员工资料手动刷新

## 用途与边界

注册中心管理员通过[刷新接口](../../api/user.md#刷新已有员工资料)，从钉钉读取 MySQL 已有员工的姓名和所属顶级部门，更新员工与部门表，再对姓名变化项调用中间件增量资料接口。

不导入钉钉全体员工、不删除离职或无法读取的员工、不更新头像、文件权限或管理员关系；不调用用户/管理员 full-table，也不创建用户登录。知识后端继续负责首次员工初始化、登录和启动全表注册。

---

## 流程与一致性

1. 读取 employees 的 userid、name、department_id，确定本次员工范围，不 JOIN 部门或管理员表。
2. 按受限并发读取每名员工的钉钉详情，逐级查询并缓存部门，最多向上 32 层。所有员工都通过校验后才进入写入阶段。
3. 将姓名变化的 userid 登记到 SQLite 持久待同步表。表只记录标识与随机修订标识，不复制姓名、头像或权限。
4. 在单一 MySQL 事务中锁定并重新核对员工姓名/部门快照，写入当前顶级部门名称，更新已有员工的 name 和 department_id；任何 SQL 失败均请求回滚。并发编辑或删除导致快照冲突时返回 409，不覆盖新资料。
5. MySQL 提交后重新读取当前已持久化姓名，对待同步项发送 `POST /middleware-service/api/user/update`，精确提交 userid、username，不携带 permissions。中间件确认后删除相应修订号的待同步项。
6. 无待同步项才返回 200；存在失败项返回 503，并明确数据库已完成更新。再次点击会重试历史待同步项，即使姓名已与钉钉一致，也不会漏掉失败请求。

多所属部门最终落在同一个顶级部门时正常合并；企业根部门与实际一级部门并存时忽略根部门。若分属多个顶级部门、部门链循环/断开、超过层级限制、userid 不一致、读取失败或应用通讯录权限不足，本次不提交任何员工/部门变更。

只刷新本次员工实际所属的顶级部门名称；不全量遍历组织架构、不自动删除旧的无员工部门。刷新开始后新建的员工留待下次刷新，不插入或删除员工记录。

---

## 失败与恢复

| 阶段 | 数据库状态与恢复 |
| --- | --- |
| configuration / busy / fetch / prepare_sync | 尚未执行 MySQL 更新，false；修复配置、范围或等待后重试。 |
| database | 已请求回滚；提交异常、网络或超时可能无法确认是否提交，返回 null；乐观快照冲突明确 false。待同步标记保留，重试读取当前资料。 |
| middleware_sync | MySQL 已提交，true；失败标识保留，可再次刷新，不回滚本地已确认资料。 |

中间件 401 只失效本次使用的平台 token；现有平台会话任务负责恢复，用户登录状态不在此服务中写入。中间件响应须与提交的 userid、username 一致，否则按失败保留标记。错误不回显密钥、应用 token、HTTP 原始正文或数据库连接信息。

待同步表由独立仓库惰性创建，使用现有 SQLite 连接及事务，不修改审核记录。标记先于 MySQL 写入：若进程在提交前后中断，下次始终发送 MySQL 当前姓名，不能从任务旧载荷恢复姓名。每次登记的随机修订标识避免旧确认删除新标记（含删除后重新登记）；已不在 MySQL 的用户只移除标记，不向中间件删除用户。不会启动新的自动重试或定期刷新任务。

应用进程内锁阻止同实例重复刷新并返回409；它不提供跨后端分布式锁。MySQL 更新、SQLite 标记与中间件 HTTP 无法组成跨存储原子事务，因此保留明确的部分失败状态与幂等重试路径，不能把远端超时假定为未提交。

---

## 配置与应用权限

| 配置项 | 默认 | 用途 |
| --- | --- | --- |
| `DINGTALK_APP_KEY` | 空 | 企业内部应用 AppKey，仅服务端使用。 |
| `DINGTALK_APP_SECRET` | 空 | 对应 AppSecret，不从前端传入。 |
| `DINGTALK_TIMEOUT_SECONDS` | `10` | 单次钉钉 HTTP 超时秒数。 |
| `DINGTALK_REFRESH_TIMEOUT_SECONDS` | `300` | 单次刷新总超时，覆盖资料读取、数据库和姓名同步。 |
| `DINGTALK_REFRESH_MAX_CONCURRENCY` | `5` | 同时处理员工数，范围1—20；部门查询跨员工复用缓存。 |

AppKey/AppSecret 必须同时填写；均为空不影响其他业务启动，刷新接口返回配置阶段503。只需企业内部应用凭据及成员/部门信息读取权限，并确保应用通讯录范围覆盖本次已有员工；此操作不使用免登code，因此不需要CorpId或前端应用参数。环境变量覆盖.env、不插值；修改后重启。代理/前端应为管理员手动刷新预留相应请求等待时间。

MySQL 账号需要 employees 与 departments 的 SELECT，employees 的 name/department_id UPDATE，以及 departments INSERT/UPDATE 权限。现有 SQLite 路径需可写。中间件使用 registry 平台会话及已存在的 user/update 授权。

---

## 实现、依赖与验证

- `app/config/dingtalk.py`：独立通讯录配置，不恢复旧登录配置。
- `app/infrastructure/dingtalk.py`：缓存应用 access_token，调用员工和部门只读接口，失效后最多刷新一次，不跟随重定向或使用环境代理。
- `app/service/personnel_refresh.py`：预读取校验、并发限制、事务阶段、增量发送及错误状态。
- `app/repository/employee.py`：单表目标读取与原子资料更新；`app/repository/personnel_sync.py`：SQLite 标记与修订确认。
- `app/main.py`：共用人员仓库、SQLite和平台会话，关闭钉钉HTTP客户端；启动不执行刷新。

精确表和结果字段见[刷新与待同步契约](../../architecture/data/personnel-refresh.md)。测试使用 tests/tmp、SQL/HTTP替身，覆盖范围、部门层级、权限保留、失败前置、事务回滚、提交后同步、持久重试、取消、超时和管理员边界；没有使用真实钉钉、数据库或共享中间件做变更。

钉钉参考：[获取企业应用 token](https://open.dingtalk.com/document/orgapp-server/obtain-orgapp-token)、[查询用户详情](https://open.dingtalk.com/document/orgapp-server/query-user-details)、[部门详情](https://developer.alibaba.com/docs/api.htm?apiId=52330)。读取适配与知识后端既有实现保持相同的企业根部门/顶级部门规则，不修改来源项目。
