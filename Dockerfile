FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
# 先装依赖再拷代码：只改代码时重建不用重新下载依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app

ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 CHUHAI_DB=/app/data/chuhai.db
RUN useradd --system --uid 10001 app && mkdir -p /app/data && chown app /app/data
USER app
VOLUME /app/data
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/robots.txt', timeout=3)"
# 只有一个进程：SQLite 单写者，多 worker 没有收益
# forwarded-allow-ips=*：端口只绑在宿主 127.0.0.1，能连进来的只有本机反代，用来识别 https
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]
