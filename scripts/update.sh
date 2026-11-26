#!/usr/bin/env bash
# 법령 데이터 갱신 — 목록을 다시 훑어 개정·신규 판만 받고, 별표 텍스트·관계를 보충한 뒤 lawdb.sqlite 를 다시 만든다.
#   scripts/update.sh            # 전체 (중간에 끊기면 다시 실행하면 이어서 한다)
#   LAW_MCP_RESTART="podman compose -f deploy/compose.yaml restart law-mcp" scripts/update.sh
# 필요: .env 의 LAW_API_OC, 디스크 여유(새 DB·낱말 색인 하나만큼 ≈ 15GB). 개정 전 판은 지우지 않고 색인에서만 뺀다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
unset VIRTUAL_ENV
PY=(uv run -q --no-project --with olefile --with lxml python)
LOG="$ROOT/logs/update-$(date +%Y%m%d-%H%M%S).log"; mkdir -p "$ROOT/logs"
exec > >(tee -a "$LOG") 2>&1
step() { echo; echo "=== $(date '+%F %T') $*"; }

# FROM=annex|links|build 로 앞 단계를 건너뛰고 이어서 할 수 있다 (중간에 끊겼을 때)
FROM="${FROM:-list}"
if [ "$FROM" = list ]; then
for t in law ordin; do
  step "목록 다시 훑기·새 판 본문 받기 ($t)"
  "${PY[@]}" scripts/fetch_lawdata.py all --target "$t" --relist
done
fi
if [ "$FROM" = list ] || [ "$FROM" = annex ]; then
for t in law ordin; do
  step "별표 텍스트 ($t)"
  "${PY[@]}" scripts/fetch_annex.py all --target "$t" --retry-empty
  "${PY[@]}" scripts/fetch_annex.py relink --target "$t"
done
fi
if [ "$FROM" != build ]; then
step "법령체계도 (아직 체계도에 없는 법령만)"
"${PY[@]}" scripts/fetch_links.py stmd
step "법령–조례 조문 연계 (색인이 쓰는 관계만, 새로 받아 교체)"
"${PY[@]}" scripts/fetch_links.py ordjo --fresh
fi
# 새 DB 와 딸린 색인(낱말·임베딩)을 옆에 다 만들고 검사를 통과하면 함께 바꿔 끼운다 — 조문 번호가 빌드마다 새로
# 매겨지므로 따로 바꾸면 딸린 색인이 다른 조문을 가리킨다. 검사에 걸리면 기존 DB 를 그대로 둔다.
DB="$ROOT/data/lawdb.sqlite"; WORDS="$ROOT/data/lawdb_words.sqlite"; VEC="$ROOT/data/lawdb_vec"
step "색인 다시 만들기 (옆에 만든다: $DB.new)"
"${PY[@]}" scripts/build_index.py --out "$DB.new"
step "새 색인 검사"
"${PY[@]}" scripts/check_index.py --new "$DB.new" --old "$DB" --min-ratio "${INDEX_MIN_RATIO:-0.9}" \
  || { echo "새 색인에 문제가 있어 바꾸지 않았습니다 — $DB.new 를 확인하세요."; exit 1; }
if [ -f "$WORDS" ]; then
  step "낱말 색인 다시 만들기"
  rm -f "$WORDS.new" "$WORDS.new-wal" "$WORDS.new-shm"
  (cd "$ROOT/mcp" && uv run -q python ../scripts/build_words.py --db "$DB.new" --out "$WORDS.new")
fi
if [ -d "$VEC" ]; then
  step "임베딩 색인 갱신 (글이 바뀐 조문만 다시 임베딩)"
  rm -rf "$VEC.new" && cp -r "$VEC" "$VEC.new"
  (cd "$ROOT/mcp" && uv run -q python ../scripts/build_vectors.py --db "$DB.new" --out "$VEC.new")
fi
step "바꿔 끼우기"
mv -f "$DB.new" "$DB"
if [ -f "$WORDS.new" ]; then rm -f "$WORDS-wal" "$WORDS-shm"; mv -f "$WORDS.new" "$WORDS"; fi
if [ -d "$VEC.new" ]; then rm -rf "$VEC.old" && mv "$VEC" "$VEC.old" && mv "$VEC.new" "$VEC" && rm -rf "$VEC.old"; fi
if [ -n "${LAW_MCP_RESTART:-}" ]; then
  step "law-mcp 재시작: $LAW_MCP_RESTART"
  eval "$LAW_MCP_RESTART"
else
  echo "law-mcp 가 새 DB 를 읽으려면 재시작하세요 (LAW_MCP_RESTART 로 자동화 가능)."
fi
step "완료 — 로그 $LOG"
