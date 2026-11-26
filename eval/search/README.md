# 검색 평가

law_search 가 정답 조문을 몇 위에 놓는지 잰다. 모델·에이전트 없이 검색만 본다.

| 묶음 | 만드는 법 | 재는 것 |
|---|---|---|
| `sets/auto300.json` | `make_set.py` — 제목이 드문 법령 조문 300개를 무작위로 | `clean`(제목 그대로) · `noisy`(제목 + 흔한 낱말) |
| `sets/daily270.json` | `make_daily_set.py` — 같은 조문마다 모델이 일상어 질문을 만든다 | `q`(법령 용어를 쓰지 않은 질문) |

```
cd mcp
SEARCH_METHOD=trigram        .venv/bin/python ../eval/search/score.py ../eval/search/sets/daily270.json --out ../eval/search/out/daily-trigram.json
SEARCH_METHOD=trigram+vector .venv/bin/python ../eval/search/score.py ../eval/search/sets/daily270.json --out ../eval/search/out/daily-vector.json
```

`vector` 는 `data/lawdb_vec/`(`scripts/build_vectors.py`)와 임베딩 서버(`EMBED_URL`)가 있어야 한다.
