"""검색 평가 묶음을 MCP 호출 기록에서 뽑는다 — 모델이 law_search 바로 다음(30초 안)에 law_article 로 연 조문을 그 검색어의
정답으로 둔다. 실제로 쓰인 검색어라 자주 묻는 핵심 조문이 많다(make_set.py 의 무작위 조문 묶음과 보완).
치우침: 모델은 주로 그때 검색 결과에 보인 조문을 열었으므로 당시 방식(trigram)에 유리하다 — shown 으로 표시한다."""
import argparse, json, re

ap = argparse.ArgumentParser()
ap.add_argument("--trace", default="/home/ubuntu/permit/law/logs/law_mcp_trace.jsonl")
ap.add_argument("--out", required=True)
ap.add_argument("--gap", type=float, default=30.0)
a = ap.parse_args()
root = lambda s: (re.match(r"(제\s*\d+\s*조(?:의\s*\d+)?|별표\s*\d+(?:의\s*\d+)?)", s or "") or [s])[0].replace(" ", "")
rows = [json.loads(l) for l in open(a.trace, encoding="utf-8")]
seen, cases = set(), []
for r, nxt in zip(rows, rows[1:]):
    if r["tool"] != "law_search" or nxt["tool"] != "law_article" or nxt["ts"] - r["ts"] > a.gap:
        continue
    q, law, art = r["args"].get("query"), nxt["args"].get("law"), root(nxt["args"].get("article"))
    key = (q, law, art)
    if not q or not law or not art or key in seen:
        continue
    seen.add(key)
    shown = any(x[0] == law and root(x[1]) == art for x in r.get("refs") or [])
    cases.append({"q": q, "args": {k: v for k, v in r["args"].items() if k in ("scope", "region") and v},
                  "law": law, "article": art, "title": "", "shown": shown})
json.dump({"cases": cases}, open(a.out, "w"), ensure_ascii=False, indent=1)
print(len(cases), "개, 당시 결과에 보였던 것", sum(c["shown"] for c in cases))
