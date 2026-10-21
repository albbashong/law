#!/usr/bin/env bash
# law-mcp(7017)와 law_review 에이전트(7016)를 백그라운드로 띄운다. 로그는 logs/.
#   agent/scripts/run.sh start|stop|status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOGS="$ROOT/logs"; mkdir -p "$LOGS"
unset VIRTUAL_ENV
start() {
  (cd "$ROOT/mcp" && LAW_MCP_TRACE_FILE="${LAW_MCP_TRACE_FILE:-$LOGS/law_mcp_trace.jsonl}" nohup uv run python -m law_mcp.server > "$LOGS/law_mcp.log" 2>&1 & echo $! > "$LOGS/law_mcp.pid")
  (cd "$ROOT/agent/runtime" && AGENT_DIR="$ROOT/agent/law_review" nohup uv run python src/main.py \
      > "$LOGS/law_review.log" 2>&1 & echo $! > "$LOGS/law_review.pid")
  echo "started: law-mcp :7017, law_review :7016"
}
stop() {
  for n in law_review law_mcp; do
    if [ -f "$LOGS/$n.pid" ]; then pkill -TERM -P "$(cat "$LOGS/$n.pid")" 2>/dev/null || true
      kill "$(cat "$LOGS/$n.pid")" 2>/dev/null || true; rm -f "$LOGS/$n.pid"; fi
  done
  echo stopped
}
status() {
  curl -s -m 3 http://localhost:7017/health || echo "law-mcp: down"; echo
  curl -s -m 3 http://localhost:7016/.well-known/agent-card.json | head -c 120 || echo "law_review: down"; echo
}
"${1:-status}"
