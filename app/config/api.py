"""注册中心对外业务 API 前缀，路由与响应中的资源链接共用。"""

import os

APPLICATION_PREFIX = f'{os.environ.get("APP_PATH_PREFIX", "")}/knowledge-accumulation'
API_PREFIX = f'{APPLICATION_PREFIX}/registry/api'
