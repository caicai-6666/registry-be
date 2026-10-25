# MySQL 与统一部署

本项目使用 MySQL 作为关系型数据库。镜像构建和运行部署由与后端同级的 `knowledge-accumulation-deploy` 项目维护，MySQL 镜像位于其 `mysql` 子目录。

---

## 项目职责与入口

| 项目 | 职责 | 阅读入口 |
| --- | --- | --- |
| `knowledge-accumulation-deploy/mysql` | 维护 MySQL 镜像构建与默认字符集配置。 | [MySQL 镜像说明](../../../../knowledge-accumulation-deploy/mysql/readme.md) |
| `knowledge-accumulation-deploy` | 维护 Compose 服务、环境变量、网络和数据持久化。 | [统一部署说明](../../../../knowledge-accumulation-deploy/readme.md) |
| `registry-be` | 维护数据库访问与正式资源数据契约。 | [项目总览](../../project.md) |

---

## 当前实现边界

已提供 MySQL 8.4 容器、MyVector 插件与统一部署配置，具体配置和操作步骤以部署项目文档为准。

`departments`、`employees` 和 `platform_admins` 保存顶级部门、钉钉 userid、平台文件权限和管理员关系，定义见[公司部门与员工契约](../../architecture/data/employee.md)。首次钉钉员工初始化、用户及管理员全表注册均由知识后端负责；registry 查询同一权威人员源，并负责后续人员权限和管理员维护。当前查询需要上述表的 SELECT 权限，不能沿用旧的 registry 登录初始化写入流程；[员工资料刷新](../application/personnel-refresh.md)还需要 employees 的 name/department_id UPDATE 与 departments INSERT/UPDATE 权限，保留头像、文件权限和管理员关联。[用户授权管理](../application/user-access.md)需要employees.platform_permissions的UPDATE及platform_admins的INSERT/DELETE权限，不新增MySQL表。

后端已提供正式资源表与只读检索连接池，待审核申请持久化由 SQLite 承担，字段约束见[注册中心资源表契约](../../architecture/data/resource.md)。建表 SQL 由 `knowledge-accumulation-deploy/mysql/init/` 维护并打包进 MySQL 镜像，在空数据目录首次启动时执行。资源仓库只初始化连接池，不执行建表；已有数据卷不会重跑脚本，当前仅保留最终版本初始化定义，不再提供历史 MySQL 迁移文件；旧结构处理边界见[资源表说明](../../architecture/data/resource.md#初始化与版本边界)。

后端已提供[服务入口与容器配置](../application/runtime.md)，并接入 Compose，等待 MySQL 健康检查通过后启动。消息中间件部署仍由对应项目维护。

---

## 后端连接配置

`app/config/database.py` 在启动时读取配置，进程环境优先于项目根目录 `.env`。消费启用且中间件配置完整时，数据库密码必填。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MYSQL_HOST` | `127.0.0.1` | MySQL 地址。 |
| `MYSQL_PORT` | `3306` | 数据库端口。 |
| `MYSQL_DATABASE` | `registry` | 业务数据库。 |
| `MYSQL_USER` | `registry` | 业务账号。 |
| `MYSQL_PASSWORD` | 空，启用消费时必填 | 业务账号密码。 |
| `MYSQL_CONNECT_TIMEOUT_SECONDS` | `5` | 建立连接的超时秒数。 |
| `MYSQL_OPERATION_TIMEOUT_SECONDS` | `10` | 连接池初始化或单次仓库操作的总超时秒数。 |

连接池采用 `utf8mb4`，会话时区设置为 UTC，事务提交后才允许确认中间件消息。后台接收模块的启停和恢复行为见[入库申请接收服务](../application/ingestion-consumer.md)。
