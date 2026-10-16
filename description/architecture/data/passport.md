# 文件通行证数组契约

`passport` 表示一份文件持有的通行证字符串数组；一个通行证也可以用于多份文件。直接保存在资源及审核记录中，不建立通行证关联表。运行时校验与 JSON 编解码由 `app/model/passport.py` 统一维护。

---

## 值与边界

```json
{"passport": ["通行证甲", "通行证乙"]}
```

| 场景 | 契约 |
| --- | --- |
| 人工批准入库 | 接收非空数组；省略或 null 时生成一个 UUID 通行证，保存为单元素数组。 |
| 人工或自动拒绝入库 | 反馈与新审核记录保存 `[]`，表示没有签发通行证。 |
| 文件目录及审核历史 | 返回数组；升级前完全未知的值保留 null，与已知空数组区分。 |
| 删除申请 | 至少一个通行证，要求与正式文件的完整通行证集合一致。 |
| 推荐条目与推荐分组 | 每条仍是一个通行证字符串；一份文件可进入多个通行证分组。 |

每项必须是非空白字符串，最多 512 字符；限制针对每一项，不限制整个数组的 JSON 文本为 512 字符。不裁剪、不去重、不拆分逗号或看起来像 JSON 的业务字符串，保留数组顺序和重复项。人工审核 HTTP 请求严格接收数组，旧单字符串不再作为新人工请求的有效类型。

中间件沿用字段名 passport，入库结果发布数组，删除消息使用数组。删除消息在消费边界兼容中间件交付的历史单字符串，将其转为单元素数组；空值仍不能作为有效删除申请。上游完整契约见[中间件通行证集合](../../../../../知识沉淀中间件服务/middleware-service/description/architecture/data/passport.md)。

---

## 存储与一致性

MySQL `resources.passport` 使用可空 JSON 列，SQLite 两张审核表的 passport 使用可空 JSON 数组文本。仓库写入时编码、读取时解码，对 HTTP、服务及模型候选返回 Python 列表，不把 JSON 文本作为字符串再次返回。

数据库约束保证数组类型；应用对元素类型、长度与空白执行校验。MySQL 原单字符串索引移除，不以整个数组建立唯一约束。文件身份仍由资源 ID、平台与 source_id 确定。

人工决定保留首次提交的数组及顺序，相同决定重试不重签通行证；改变数组内容或顺序视为改变审核决定。删除是整份文件的操作，核对通行证全集是否一致，比较时忽略顺序及重复项；子集、额外值或未知集合都不能批准删除。SQLite 保存删除申请提交的原数组，不为每个通行证拆分申请、结果或 ACK。

推荐沿用每份文件一条 HNSW 索引记录与文件 Top-K，在召回后把文件分配到其持有的每个通行证组；单个数组里的重复值不会让文件在同组出现多次。推荐输出仍为独立的 `{passport, reason}` 条目列表，最终审核可选择多项。

---

## 已有数据库升级

升级转换保留业务值：旧非空字符串变为单元素数组，旧空字符串变为 `[]`，SQL NULL 保持 NULL。旧字符串即使是 `a,b` 或 `["a","b"]`，也作为一个完整字符串保留，不擅自拆成多个通行证。

SQLite 启动自动升级到 schema 7，在同一事务中重建两张表的数组约束，保留记录、索引、自增进度、审核决定与反馈凭据；不会重新发布旧结论。无效的历史空白值导致事务回滚，保留原库等待核对。

MySQL 新容器通过[初始化 SQL](../../../../registry-deploy/mysql/init/001-resources.sql)直接建 JSON 列。已有数据库停止后端写入并备份后，在 registry-be 根目录执行：

```bash
python scripts/migrate_passport_arrays.py --apply
```

迁移脚本先锁表，在原表新增 passport_array 暂存列，保留原 passport，填充并核对全部资源 ID、值与更新时间，再执行[010 数组切换](../../../../registry-deploy/mysql/migrations/010-resource-passport-arrays.sql)。暂存阶段中断可重复执行；JSON 数组列已就绪时仅校验。此脚本不启动模型或访问文件，不迁移消息队列。

更早的资源表先按[资源表旧库升级](resource.md#旧库升级)完成审核拆分与正式表简化，再执行数组升级。MySQL DDL 不是跨数据库事务；SQLite 自动升级不替代 MySQL 显式迁移。

---

## 验证

```bash
python -m unittest tests.test_passport_arrays tests.test_ingestion_review_api tests.test_deletion_consumer tests.test_passport_search
REGISTRY_TEST_MYSQL_PORT=临时端口 python -m unittest tests.test_resource_mysql
REGISTRY_TEST_MYVECTOR_PORT=临时端口 python -m unittest tests.test_passport_hnsw
```

测试隔离数据均在 tests/tmp 或专用临时数据库容器。覆盖数组校验、跨流程保留多个值、拒绝空数组、删除全集核对、多组聚合，以及旧库转换、迁移失败回滚和重复执行；真实 HNSW 验证数组元数据及跨组成员，不调用真实模型。
