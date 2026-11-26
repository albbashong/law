#!/bin/bash
# 검색 방식 비교 — 묶음 × 방식마다 score.py 를 돌려 eval/search/out/ 에 남긴다.  bash eval/search/run_compare.sh trigram trigram+vector
cd "$(dirname "$0")/../../mcp"
for m in "$@"; do
  for s in daily270 auto300; do
    echo "== $m $s"
    SEARCH_METHOD="$m" .venv/bin/python ../eval/search/score.py ../eval/search/sets/$s.json --out "../eval/search/out/$s-${m//+/_}.json" | tail -n 1
  done
done
