#!/usr/bin/env bash
# law-mcp(7017) · law_review 에이전트(7016) · search_plan 검색 계획 에이전트(7018)를 백그라운드로 띄운다. 로그는 logs/.
#   agent/scripts/run.sh start|stop|status
# 작은 모델 비교용 law_review 를 하나 더(ollama 등 OpenAI 호환, 주소는 SMALL_BASE — 기본 이 PC ollama):
#   agent/scripts/run.sh start-small <이름> <모델> <포트> [<계획 포트>]   계획 포트를 주면 같은 모델의 search_plan 도 띄워 잇는다
#   agent/scripts/run.sh stop-small <이름>
# ollama 는 문맥을 넓혀 띄워야 한다(기본 문맥이 짧아 시스템 문장이 잘린다): OLLAMA_CONTEXT_LENGTH=32768 ollama serve
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOGS="$ROOT/logs"; mkdir -p "$LOGS"
unset VIRTUAL_ENV
SMALL_BASE="${SMALL_BASE:-http://localhost:${OLLAMA_PORT:-9001}/v1}"

agent() {   # agent <pid 이름> <설정 폴더> [환경변수=값 ...]
  local name="$1" dir="$2"; shift 2
  (cd "$ROOT/agent/runtime" && env AGENT_DIR="$ROOT/agent/$dir" "$@" nohup uv run python src/main.py \
      > "$LOGS/$name.log" 2>&1 & echo $! > "$LOGS/$name.pid")
}
kill_pid() {   # 끝날 때까지 기다린다 — 안 기다리면 곧바로 띄운 새 프로세스가 포트를 못 잡는다
  local n="$1" pid kids
  if [ -f "$LOGS/$n.pid" ]; then pid="$(cat "$LOGS/$n.pid")"; kids="$(pgrep -P "$pid" 2>/dev/null || true)"
    kill -TERM $kids "$pid" 2>/dev/null || true; rm -f "$LOGS/$n.pid"
    for _ in $(seq 50); do kill -0 $kids "$pid" 2>/dev/null || break; sleep 0.2; done; fi
}
start() {
  (cd "$ROOT/mcp" && LAW_MCP_TRACE_FILE="${LAW_MCP_TRACE_FILE:-$LOGS/law_mcp_trace.jsonl}" nohup uv run python -m law_mcp.server > "$LOGS/law_mcp.log" 2>&1 & echo $! > "$LOGS/law_mcp.pid")
  agent law_review law_review
  agent search_plan search_plan
  echo "started: law-mcp :7017, law_review :7016, search_plan :7018"
}
stop() {
  for n in law_review search_plan law_mcp; do kill_pid "$n"; done
  echo stopped
}
start_small() {
  local name="$1" model="$2" port="$3" plan_port="${4:-}"
  # 작은 모델은 생각을 끄고(none — qwen3 는 low 도 생각을 켜 수천 토큰을 쓴다) 출력 상한을 둔다(실측 2026-11-16 7분+ 멈춤)
  local llm=(VLLM_BASE="$SMALL_BASE" VLLM_MODEL="$model" VLLM_API_KEY="${SMALL_KEY:-ollama}"
             REASONING_EFFORT="${SMALL_EFFORT:-none}" VLLM_MAX_TOKENS="${SMALL_MAX_TOKENS:-4096}")
  local mcp="MCP_SERVERS={\"law\":\"http://localhost:7017/mcp?caller=$name\"}"   # 평가가 이 에이전트의 조회만 골라 보게(--caller)
  if [ -n "$plan_port" ]; then
    agent "${name}_plan" search_plan PORT="$plan_port" AGENT_NAME="${name}_plan" "${llm[@]}"
    agent "$name" law_review PORT="$port" AGENT_NAME="$name" PLAN_AGENT_URL="http://localhost:$plan_port" "$mcp" "${llm[@]}"
    echo "started: $name :$port ($model, 계획 :$plan_port)"
  else
    agent "$name" law_review PORT="$port" AGENT_NAME="$name" PLAN_AGENT_URL= "$mcp" "${llm[@]}"
    echo "started: $name :$port ($model, 계획 없음)"
  fi
}
stop_small() { kill_pid "$1"; kill_pid "$1_plan"; echo "stopped: $1"; }
status() {
  curl -s -m 3 http://localhost:7017/health || echo "law-mcp: down"; echo
  curl -s -m 3 http://localhost:7016/.well-known/agent-card.json | head -c 120 || echo "law_review: down"; echo
  curl -s -m 3 http://localhost:7018/.well-known/agent-card.json | head -c 120 || echo "search_plan: down"; echo
}
case "${1:-status}" in
  start-small) shift; start_small "$@" ;;
  stop-small) shift; stop_small "$@" ;;
  *) "${1:-status}" ;;
esac
