# Python 运行环境与依赖

本文说明知识沉淀注册中心后端的基础依赖及安装方式。项目根目录的 [requirement.txt](../../../requirement.txt) 是直接依赖清单。

---

## 开发环境与安装

当前开发环境使用 Conda 环境 `registry-be`，Python 版本为 `3.12.15`。后续部署环境以 Python `3.12` 为基线。

在项目根目录执行：

```bash
conda activate registry-be
python -m pip install -r requirement.txt
python -m pip check
```

若环境尚未创建，先执行：

```bash
conda create -n registry-be python=3.12 pip
```

`requirement.txt` 固定已安装并验证的直接依赖版本；新增或升级依赖时，应重新检查解析结果、导入和相关功能兼容性。

---

## 依赖职责

| 依赖 | 用途 |
| --- | --- |
| `openai` | 使用官方 SDK 的 `AsyncOpenAI` 调用模型接口。 |
| `aiomysql[rsa]` | 异步连接 MySQL，并提供 RSA 认证依赖。 |
| `fastapi` | 构建后端 HTTP API。 |
| `uvicorn[standard]` | 运行 ASGI 服务，包含标准运行扩展。 |
| `httpx` | 异步调用中间件 HTTP 接口。 |
| `python-dotenv` | 读取项目根目录的环境配置。 |
| `pydantic` | 校验中间件配置和会话响应，隐藏敏感字段。 |
| `aiosqlite` | 异步访问 SQLite 数据库。 |
| `llama-index` | 提供知识索引及相关集成能力。 |
| `llama-index-embeddings-openai-like` | 使用 LlamaIndex 异步调用 vLLM 的 OpenAI 兼容 embedding 接口。 |
| `langgraph` | 编排具有状态的智能体工作流。 |
| `PyMuPDF` | 在内存中逐页渲染资源 PDF，生成带页面 UUID 的 PNG 对象。 |

SQLite 引擎通过 Python 标准库 `sqlite3` 使用，无须另行安装名为 `sqlite` 的 Python 包。MySQL 开发容器和连接入口见 [MySQL 与统一部署](../infrastructure/mysql.md)。

---

## 使用边界

已提供可运行的 FastAPI 入口，启动与容器配置见 [环境配置与容器运行](runtime.md)。[入库申请接收服务](ingestion-consumer.md)将待审核申请暂存 SQLite，MySQL 保存审核通过的正式资源。[人员系统](personnel.md)提供开发者 token、钉钉 H5 免登与审核身份依赖，[人工入库审核服务](ingestion-review.md)已接入这些身份。

应用生命周期已接入[中间件注册与心跳](../infrastructure/middleware-session.md)，启动时登录并在后台续期，关闭时释放资源。

实际调用 OpenAI 服务时需配置 `OPENAI_API_KEY`；数据库凭据使用本地部署配置，不写入依赖文件或文档。基础单元测试不发起真实模型请求；[MyVector 试用](../infrastructure/myvector.md)脚本会显式调用本地配置的 vLLM embedding 服务。

OpenAI 异步客户端的使用方式以 [OpenAI 官方 Python SDK 文档](https://developers.openai.com/api/reference/python)为准；MySQL 驱动参考 [aiomysql 项目说明](https://pypi.org/project/aiomysql/)。
