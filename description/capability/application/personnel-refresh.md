# 已有员工资料手动刷新

## 用途与边界

注册中心管理员通过[刷新接口](../../api/user.md#刷新已有员工资料)，从钉钉读取 MySQL 已有员工的姓名、所属顶级部门及任职资料（直属主管 userid、职位），更新员工与部门表及在职状态，再为本次全部目标和历史待同步项发送当前资料或状态。

不导入钉钉全体员工、不删除离职或无法读取的员工、不更新头像、文件权限或管理员关系；不调用用户/管理员 full-table，也不创建用户登录。知识后端继续负责首次员工初始化、登录和启动全表注册。

---

## 流程与一致性

1. 读取 employees 的 userid、name、department_id、department_ids、work_profile、employment_status，确定本次员工范围，不 JOIN 部门或管理员表。
2. 按受限并发读取每名员工的钉钉详情，逐级查询并缓存部门，最多向上 32 层。员工详情明确返回不存在（errcode=60121）时跳过资料采集，登记离职状态候选；其余员工都通过校验后才进入写入阶段。
3. 将本次全部目标员工（包括明确不存在的员工）的 userid 登记到 SQLite 持久待同步表。表只记录标识与随机修订标识，不复制资料、状态、头像或权限载荷。
4. 在单一 MySQL 事务中锁定并重新核对员工姓名、部门、任职资料及在职状态快照，写入当前顶级部门名称，对查到资料的员工更新 name、department_id、department_ids、work_profile 并设置 employment_status=employed，对明确不存在的员工只设置 employment_status=resigned；任何 SQL 失败均请求回滚。并发编辑或删除导致快照冲突时返回 409，不覆盖新资料。
5. MySQL 提交后回读当前资料及状态，对所有待同步项发送 `POST /middleware-service/api/user/update`：在职发送 userid、username、departments、employment_status；离职只发送 userid、employment_status，不发送其保留的旧资料。两者都不携带 permissions。中间件确认各请求值（包括状态）一致后才按修订号删除标记，不能提前清除离职任务。
6. 无待同步项才返回 200；存在失败项返回 503，并明确数据库已完成更新。再次点击会重试历史待同步项，即使姓名已与钉钉一致，也不会漏掉失败请求。

多所属部门最终落在同一个顶级部门时正常合并；企业根部门与实际一级部门并存时忽略根部门。多个一级部门全部保留；若部门链循环/断开、超过层级限制、userid 不一致、其他读取失败或应用通讯录权限不足，本次不提交任何员工资料、状态或部门变更。

跳过仅依据员工详情接口的明确不存在错误，不根据错误文本、HTTP 404、部门不存在或通讯录权限不足推断离职。明确不存在时保留 MySQL 记录、原姓名、部门、任职资料、头像、权限及管理员关系，仅设置离职，不撤销中央登录；响应 `skipped_users` 统计跳过资料采集并标记离职的人数。全部员工均不存在时仍提交离职状态，同步确认后返回 200。后续刷新仍包含离职员工，重新查到且校验通过即恢复在职并更新资料。

只刷新本次员工实际所属的顶级部门名称；不全量遍历组织架构、不自动删除旧的无员工部门。刷新开始后新建的员工留待下次刷新，不插入或删除员工记录。

---

## 失败与恢复

| 阶段 | 数据库状态与恢复 |
| --- | --- |
| configuration / busy / fetch / prepare_sync | 尚未执行 MySQL 更新，false；修复配置、范围或等待后重试。 |
| database | 已请求回滚；提交异常、网络或超时可能无法确认是否提交，返回 null；乐观快照冲突明确 false。待同步标记保留，重试读取当前资料。 |
| middleware_sync | MySQL 已提交，true；失败标识保留，可再次刷新，不回滚本地已确认资料。 |

中间件 401 只失效本次使用的平台 token；现有平台会话任务负责恢复，用户登录状态不在此服务中写入。中间件响应须与提交的 userid 及实际发送的 username、departments、employment_status 一致，否则按失败保留标记。错误不回显密钥、应用 token、HTTP 原始正文或数据库连接信息。

待同步表由独立仓库惰性创建，使用现有 SQLite 连接及事务，不修改审核记录。包括离职员工在内的全部目标，标记先于 MySQL 写入：若进程在提交前后中断，下次始终发送 MySQL 当前资料和在职状态，不能重放旧状态。每次登记的随机修订标识避免旧确认删除新标记（含删除后重新登记）；已不在 MySQL 的用户只移除标记，不向中间件删除用户。不会启动新的自动重试或定期刷新任务。

应用进程内锁阻止同实例重复刷新并返回409；它不提供跨后端分布式锁。MySQL 更新、SQLite 标记与中间件 HTTP 无法组成跨存储原子事务，因此保留明确的部分失败状态与幂等重试路径，不能把远端超时假定为未提交。

---

## 配置与应用权限

| 配置项 | 默认 | 用途 |
| --- | --- | --- |
| `DINGTALK_APP_KEY` | 空 | 企业内部应用 AppKey，仅服务端使用。 |
| `DINGTALK_APP_SECRET` | 空 | 对应 AppSecret，不从前端传入。 |
| `DINGTALK_PROXY_URL` | 空 | 仅钉钉 HTTP 请求使用的 HTTP/HTTPS 代理根地址；可带代理认证，空值直连。详见下节。 |
| `DINGTALK_TIMEOUT_SECONDS` | `10` | 单次钉钉 HTTP 超时秒数。 |
| `DINGTALK_REFRESH_TIMEOUT_SECONDS` | `300` | 单次刷新总超时，覆盖资料读取、数据库和资料及状态同步。 |
| `DINGTALK_REFRESH_MAX_CONCURRENCY` | `5` | 同时处理员工数，范围1—20；部门查询跨员工复用缓存。 |

AppKey/AppSecret 必须同时填写；均为空不影响其他业务启动，刷新接口返回配置阶段503。只需企业内部应用凭据及成员/部门信息读取权限，并确保应用通讯录范围覆盖本次已有员工；此操作不使用免登code，因此不需要CorpId或前端应用参数。环境变量覆盖.env、不插值；修改后重启。代理/前端应为管理员手动刷新预留相应请求等待时间。

MySQL 账号需要 employees 与 departments 的 SELECT，employees 的 name/department_id/department_ids/work_profile/employment_status UPDATE，以及 departments INSERT/UPDATE 权限。现有 SQLite 路径需可写。中间件使用 registry 平台会话及已存在的 user/update 授权。

---

## 钉钉固定出口代理

`DingTalkSettings.proxy_url` 通过 `DINGTALK_PROXY_URL` 配置，使用 `SecretStr` 保存，模型打印和配置校验错误不回显输入。地址必须使用 `http://` 或 `https://`，允许根路径 `/`、端口及代理用户名密码，不接受业务路径、查询参数、片段、空白或 SOCKS 协议。未填写保持直连；进程环境优先于 `.env`，显式空环境变量可覆盖文件配置，读取不展开 `$`。

`DingTalkClient` 以 `httpx.AsyncClient(proxy=..., trust_env=False, verify=True)` 创建唯一连接池。忽略系统 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`；保留目标 HTTPS 与 HTTPS 代理证书校验，不跟随重定向。代理无法连接、拒绝 CONNECT 或要求认证时返回既有脱敏依赖错误，不自动回退直连。注入测试 `transport` 与非空代理互斥，防止测试误以为已经覆盖代理链路。

| 请求范围 | 是否使用此代理 |
| --- | --- |
| 应用 token：`api.dingtalk.com/v1.0/oauth2/accessToken` | 是，包含 token 失效后重新获取。 |
| 员工详情：`oapi.dingtalk.com/topapi/v2/user/get` | 是。 |
| 部门详情：`oapi.dingtalk.com/topapi/v2/department/get` | 是。 |
| 钉钉用户登录、机器人 HTTP、Stream/WebSocket | 本项目未实现；知识后端的同类能力由其自行配置，本变量不跨项目生效。 |
| 头像代理、入库附件下载、中间件及模型请求 | 否，使用各自客户端；本项目没有钉钉文件下载调用。 |

底层 `httpcore` DEBUG 日志可能包含代理响应、认证头或异常原文，钉钉请求期间通过 `ContextVar` 和日志过滤器抑制这些记录，不影响并行的其他业务请求；`httpx` 的通讯录 URL 日志继续将 `access_token` 脱敏。应用错误不记录代理 URL、凭据或 token。

本机经已有 SSH 隧道使用固定出口时，在启动进程环境设置（端口以实际隧道为准）：

```bash
DINGTALK_PROXY_URL=http://127.0.0.1:18888 python app/main.py
```

代理需允许 `api.dingtalk.com:443` 和 `oapi.dingtalk.com:443` 的 CONNECT。配置变化后重启客户端所在进程；容器内的 `127.0.0.1` 指容器自身，不能直接复用宿主机回环隧道地址。本功能不建立隧道、不配置服务器或共享部署网络。

以下只读验证直接使用项目封装和本地应用凭据，不启动应用生命周期、数据库刷新、Stream 或机器人；只输出成功标记，不输出 token 或部门资料：

```bash
DINGTALK_PROXY_URL=http://127.0.0.1:18888 python -B - <<'PY'
import asyncio
from app.config.dingtalk import load_dingtalk_settings
from app.infrastructure.dingtalk import DingTalkClient
from app.auth.error import AuthUnavailable

async def main():
    client = DingTalkClient(load_dingtalk_settings())
    try:
        await client.get_department(1)  # 首次查询先获取应用 token，两次请求均使用代理。
        print('token and department read: success')
    except AuthUnavailable:
        print('readonly probe: failed (AuthUnavailable)')
        raise SystemExit(1)
    finally:
        await client.close()

asyncio.run(main())
PY
```

---

## 实现、依赖与验证

- `app/config/dingtalk.py`：独立通讯录配置，不恢复旧登录配置。
- `app/infrastructure/dingtalk.py`：缓存应用 access_token，调用员工和部门只读接口，失效后最多刷新一次，统一使用显式代理配置，不跟随重定向或使用系统代理。
- `app/service/personnel_refresh.py`：预读取校验、并发限制、事务阶段、增量发送及错误状态。
- `app/repository/employee.py`：单表目标读取与原子资料更新；`app/repository/personnel_sync.py`：SQLite 标记与修订确认。
- `app/main.py`：共用人员仓库、SQLite和平台会话，关闭钉钉HTTP客户端；启动不执行刷新。

任职资料字段、初始化与迁移要求见[员工表契约](../../architecture/data/employee.md#任职资料与初始化)。每次显式刷新都会登记全部目标的资料与状态同步，主管和职位本身不发往中央。

本地 `tests/test_personnel_departed.py` 使用内存 HTTP/仓库替身验证部分或全部员工不存在时的状态提交与同步、空员工列表、token 重试，以及权限/网络/格式错误仍中止；不连接真实钉钉，也不写入应用数据目录。

代理回归命令为 `python -B -m unittest discover -s tests -p 'test_dingtalk_proxy.py' -v`。测试使用内存响应及本机临时 CONNECT 代理，覆盖两类钉钉域名、代理认证、407/502/连接失败不回退、TLS 校验、环境变量隔离及日志脱敏；配置样本仅写入 `tests/tmp/` 并自动清理。测试本身不访问真实钉钉，不读取实际凭据。

本地 `tests/test_employment_status.py` 补充恢复在职、离职同步失败持久重试、确认响应缺失或状态不一致、数据库状态并发冲突/回滚、用户列表 HTTP/OpenAPI 和新旧中央登录响应验证。所有验证使用内存替身，不运行应用生命周期或共享服务。

本地任职资料回归使用内存 HTTP/SQL 替身，不启动应用生命周期、不读写 `data/`。执行 `python -B -m unittest discover -s tests -p 'test_personnel_work_profile.py' -v`，覆盖字段读取与校验、空值清除、仅任职资料更新、姓名同步保留、任职资料并发冲突及 SQL 失败回滚；测试文件按项目规则仅本地保留。

精确表和结果字段见[刷新与待同步契约](../../architecture/data/personnel-refresh.md)。测试使用 tests/tmp、SQL/HTTP替身，覆盖范围、部门层级、权限保留、失败前置、事务回滚、提交后同步、持久重试、取消、超时和管理员边界；没有使用真实钉钉、数据库或共享中间件做变更。

钉钉参考：[获取企业应用 token](https://open.dingtalk.com/document/orgapp-server/obtain-orgapp-token)、[查询用户详情](https://open.dingtalk.com/document/orgapp-server/query-user-details)、[部门详情](https://developer.alibaba.com/docs/api.htm?apiId=52330)。读取适配与知识后端既有实现保持相同的企业根部门/顶级部门规则，不修改来源项目。


---

## 多一级部门归属

员工详情中的每个部门沿父链归并到企业根下的一级部门，去重后按ID排序并全部保留；实际一级部门与企业根并存时忽略根，仅属于根时保留根。多个一级部门不再导致整批刷新失败。层级循环、断裂或查询失败仍中止。

`employees.department_ids` JSON数组保存完整归属，`department_id`继续保存数组首项供旧客户端兼容，不表示主部门。NULL历史记录回退单值；刷新在同一事务更新全部部门、完整归属与旧字段，乐观并发检查包含新字段。权限、头像和管理员关系不变，在职员工向中央同步姓名、全部部门名称和状态。

当前部署初始化定义已为新空卷包含多部门字段及约束；已有共享人员库缺列时，先执行一次知识后端 `schema/019-employee-departments.sql`，不能在两端重复执行。员工列表返回 `department_ids`，调用方应关联部门列表显示全部归属；认证员工投影返回 `departments`。`tests/test_personnel_work_profile.py`覆盖多部门刷新，知识后端 `experiment/multi-departments/verify_mysql.py`使用隔离临时MySQL库验证两端查询、刷新和旧快照拒绝。


---

## 中间件部门契约

中间件 `departments` 是去除首尾空白、非空且不重复的部门名称数组，不传ID或主部门。本项目从刷新提交后的员工department_ids与部门表映射全部名称，历史NULL归属回退department_id，按ID稳定顺序并对规范化同名部门去重。部门行缺失时保留待同步项，不发送残缺列表。

每次手动刷新登记全部目标，可修复中央旧资料缺部门，以及ID不变的部门改名。继续复用 `personnel_name_sync` 表与revision，无需数据库迁移；`synced_names`、`pending_names`保留前端字段名，现统计资料及状态同步的员工数，包含仅同步离职状态。并不代表所有员工资料都发生改变。

资料DTO接收新增departments；增量请求省略保留、[]清空、显式null拒绝。纯权限更新继续省略部门，因此保留中央部门。登录查询接收有效列表/离线null，兼容旧响应未包含字段的滚动升级情况，仍拒绝矛盾状态和未知字段。中央名称列表不转换成虚构部门ID，不改变registry管理员最小身份接口。

使用 `tests/test_middleware_user_departments.py` 与 `tests/test_personnel_work_profile.py` 验证真实HTTP请求形状、登录查询、权限更新、新旧响应、部门改名和失败重试；全部使用内存替身，未写真实用户数据。知识后端全表发布不属于本项目修改范围，仍须携带权威部门和在职状态；省略部门会清空中央部门，省略状态会按在职覆盖。


---

## 在职状态契约

MySQL、前端人员列表和中间件统一使用 `employment_status`，仅接受 `employed`（在职）与 `resigned`（离职），无值转换。共享库字段及迁移要求见[员工在职状态](../../architecture/data/employee.md#在职状态)。权限维护请求继续省略状态，保留中央原值；在职状态不直接改变认证或文件授权。

中间件旧资料响应可以缺少状态，便于纯权限维护滚动升级，但明确写状态的请求必须获得相同状态的回执，缺字段也按失败保留任务。离职状态仅更新已有中央资料；若中央缺少该员工而拒绝只有状态的请求，则保留待同步项，需由知识后端完成完整用户注册，不伪造姓名或权限补建。
