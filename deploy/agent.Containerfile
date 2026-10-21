# law_review 에이전트(:7016)와 시험 페이지 playground(:7018)가 같이 쓰는 이미지.
# 키·엔드포인트는 이미지에 넣지 않고 실행할 때 env 로 준다(compose 의 env_file).
# 빌드(저장소 루트에서): podman build -f deploy/agent.Containerfile -t law-review .
FROM docker.io/library/python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app/agent/runtime
COPY agent/runtime/pyproject.toml agent/runtime/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agent/runtime/src ./src
COPY agent/law_review /app/agent/law_review
COPY eval/playground.py /app/eval/playground.py

ENV AGENT_DIR=/app/agent/law_review HOST=0.0.0.0 PORT=7016
EXPOSE 7016 7018
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:7016/.well-known/agent-card.json', timeout=4).status != 200)"
CMD ["/app/agent/runtime/.venv/bin/python", "src/main.py"]
