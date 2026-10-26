#!/usr/bin/env bash
# 예약 실행용 법령 DB 갱신 — update.sh 를 겹치지 않게 돌리고, 끝나면 law-mcp 를 재시작해 새 DB 를 읽게 한다.
#   scripts/scheduled_update.sh                 # 직접 실행해도 된다 (install_update_schedule.sh 가 예약에 건다)
#   COMPOSE="podman-compose" scripts/scheduled_update.sh   # compose 도구를 정해 줄 때
# 필요: 저장소 루트 .env 의 LAW_API_OC, data/law·data/ordin(받아 둔 원문 — 바뀐 판만 새로 받는다), uv
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"     # cron 은 PATH 가 짧다
mkdir -p logs

# 이미 돌고 있으면(지난 갱신이 길어졌으면) 이번 회차는 건너뛴다
exec 9>"$ROOT/logs/update.lock"
if ! flock -n 9; then
  echo "$(date '+%F %T') 다른 갱신이 진행 중이라 건너뜁니다."
  exit 0
fi

fail() { echo "$(date '+%F %T') 갱신 중단: $*" >&2; exit 1; }

command -v uv >/dev/null || fail "uv 가 없습니다 (https://docs.astral.sh/uv/ 설치)."
grep -qE '^LAW_API_OC=.+' .env 2>/dev/null || fail "저장소 루트 .env 에 LAW_API_OC 가 없습니다."
for d in data/law data/ordin; do
  [ -d "$d" ] || fail "$d 가 없습니다 — 원문을 처음부터 다 받게 됩니다. 원본 서버에서 data/law·data/ordin·data/links 를 먼저 옮기세요."
done
if [ -n "${LAW_DATA_DIR:-}" ] && [ "$(cd "$LAW_DATA_DIR" 2>/dev/null && pwd)" != "$ROOT/data" ]; then
  fail "LAW_DATA_DIR($LAW_DATA_DIR)이 $ROOT/data 가 아닙니다 — update.sh 는 $ROOT/data/lawdb.sqlite 를 만듭니다."
fi

# 새 DB 를 옆에 만든 뒤 바꿔 끼우므로 DB 하나만큼 + 여유 2GB 가 필요하다
need_kb=$(( $(du -k data/lawdb.sqlite 2>/dev/null | cut -f1 || echo 0) + 2*1024*1024 ))
free_kb=$(df -Pk data | awk 'NR==2 {print $4}')
[ "$free_kb" -ge "$need_kb" ] || fail "디스크 여유 $((free_kb/1024/1024))GB — $((need_kb/1024/1024))GB 이상 필요합니다."

# law-mcp 재시작 명령 — 띄울 때 쓴 compose 도구와 같아야 컨테이너를 찾는다
if [ -z "${COMPOSE:-}" ]; then
  if command -v podman-compose >/dev/null; then COMPOSE="podman-compose"; else COMPOSE="podman compose"; fi
fi
export LAW_MCP_RESTART="cd '$ROOT/deploy' && $COMPOSE restart law-mcp"

scripts/update.sh

# 재시작 뒤 새 DB 를 읽었는지
port="${LAW_MCP_PORT:-7017}"
for _ in $(seq 1 30); do
  if health=$(curl -fsS "http://127.0.0.1:$port/health" 2>/dev/null); then
    echo "$(date '+%F %T') law-mcp 상태: $health"
    break
  fi
  sleep 2
done
[ -n "${health:-}" ] || fail "law-mcp 가 :$port 에서 응답하지 않습니다 — '$COMPOSE logs law-mcp' 를 확인하세요."

# 갱신 로그는 60일만 둔다
find logs -maxdepth 1 -name 'update-*.log' -mtime +60 -delete
