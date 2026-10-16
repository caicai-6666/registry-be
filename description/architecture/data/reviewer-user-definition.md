# 注册中心审核人员定义

> **用途：** 定义本项目独立的人员 YAML 及启动期内存快照。登录契约见[人员登录 API](../../api/auth.md)，配置与装配见[人员系统](../../capability/application/personnel.md)。

---

## 人员文件

默认位置为 `data/user/users.yaml`，由 `REVIEWER_USER_FILE` 覆盖；相对路径按项目根目录解析。

```yaml
users:
  - name: "注册中心审核员"
    secret_key: "替换为独立的随机密钥"
```

| 字段 | 类型与约束 | 用途 |
| --- | --- | --- |
| `users` | 非空列表 | 人员目录。 |
| `name` | 字符串；去除首尾空白后非空，最长 100 字符，目录内唯一 | 审核人显示名称及身份查找键。 |
| `secret_key` | 非空字符串，不能全为空白，目录内唯一；不去除首尾空白 | 仅凭密钥反查唯一审核人。 |

顶层及人员条目禁止额外字段。名称、密钥重复，YAML 键重复，字段类型错误或文件无法读取均使应用启动失败。数字密钥必须显式写成 YAML 字符串。

---

## 内存快照

`load_reviewer_user_catalog()` 启动时读取一次，生成不可变 `ReviewerUser` 和 `ReviewerUserCatalog`，保存到 `application.state.reviewer_user_catalog`。目录记录源路径及内容 SHA-256，支持按名称取得人员、校验名称与密钥，以及按密钥反查人员。

密钥使用 `SecretStr`，对象表示只显示掩码。加载错误不输出原始 YAML 或 Pydantic 输入；修改文件需要重启才能生效。原文件不进入 Git 或镜像构建上下文。

---

## 业务边界

这是人工审核身份目录，不替代中间件平台注册密钥。当前没有角色、组织、人员增删改 HTTP 接口或审核授权分级；[入库审核接口](../../api/ingestion-review.md)已接入登录身份。

人工入库审核通过 `ReviewerUserDependency` 取得可信名称，不接受客户端自行提交的 `reviewer`。现有自动拒绝流程继续使用固定名称“注册中心自动审核员”。
