FROM python:3.12.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production

WORKDIR /workspace

# 先安装依赖，使业务代码变化时可以复用依赖层。
COPY requirement.txt ./
RUN python -m pip install --no-cache-dir -r requirement.txt \
    && python -m pip check

# 使用普通用户运行服务，并为后续 SQLite 与其他本地数据预留可写目录。
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /workspace/data \
    && chown app:app /workspace/data

COPY --chown=app:app app ./app

USER app
EXPOSE 8000

# 当前检查最小 Web 服务入口，不代表数据库或业务功能已就绪。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=3).close()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
