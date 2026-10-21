# law-mcp (:7017) — 법령 DB(lawdb.sqlite)는 이미지에 넣지 않고 /data 볼륨으로 붙인다.
# 빌드(저장소 루트에서): podman build -f deploy/law-mcp.Containerfile -t law-mcp .
FROM docker.io/library/python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app/mcp
COPY mcp/pyproject.toml mcp/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY mcp/src ./src
RUN uv sync --frozen --no-dev

ENV LAW_DB_PATH=/data/lawdb.sqlite MCP_HOST=0.0.0.0 MCP_PORT=7017
EXPOSE 7017
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:7017/health', timeout=4).status != 200)"
CMD ["/app/mcp/.venv/bin/python", "-m", "law_mcp.server"]
