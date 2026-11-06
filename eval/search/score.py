"""검색 평가 — make_set.py 로 만든 묶음을 law_search 에 넣어 정답 조문이 몇 위에 오는지 잰다.

정답: 그 조문(법령명·조문)이거나, 제목이 같은 조문. 결과: 1위·5위·10위 안에 든 비율과 질의당 시간.
strict 는 그 조문 자체만 정답으로 센다 — 제목이 같은 시군 조례가 법령을 밀어내도 맞은 것으로 세지 않는다."""
import argparse, json, re, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("set")
ap.add_argument("--out", required=True)
ap.add_argument("--n", type=int, default=0, help="앞에서부터 이만큼만(빠른 비교용). 0 이면 전부")
ap.add_argument("--search", default="law_mcp.tools.search:run_law_search", help="모듈:함수(store, query) -> law_search 결과. 방식은 SEARCH_METHOD 로")
a = ap.parse_args()
mod, fn = a.search.split(":")
search = getattr(__import__(mod, fromlist=[fn]), fn)
from law_mcp.server import get_store
store = get_store()
norm = lambda t: re.sub(r"\s+", "", t or "")
root = lambda s: (re.match(r"(제\s*\d+\s*조(?:의\s*\d+)?|별표\s*\d+(?:의\s*\d+)?)", s or "") or [s])[0].replace(" ", "")
cases = json.load(open(a.set))["cases"][: a.n or None]
res = {k: [] for k in ("clean", "noisy", "q") if k in cases[0]}
t0 = time.perf_counter()
for i, c in enumerate(cases):
    for kind in res:
        t = time.perf_counter()
        rows = (search(store, c[kind], **c.get("args", {}))["facts"] or {}).get("결과", [])
        same = lambda r: r["law"] == c["law"] and root(r["article"]) == root(c["article"])  # noqa: E731 — 조문 단위로 맞춘다
        rank = next((k + 1 for k, r in enumerate(rows) if same(r) or (c["title"] and norm(r.get("title")) == norm(c["title"]))), None)
        strict = next((k + 1 for k, r in enumerate(rows) if same(r)), None)
        res[kind].append({"q": c[kind], "rank": rank, "strict": strict, "sec": round(time.perf_counter() - t, 3)})
    if i % 50 == 49:
        print(i + 1, round(time.perf_counter() - t0), "s", flush=True)
summary = {}
for kind, rs in res.items():
    n = len(rs)
    summary[kind] = {f"top{k}": round(sum(1 for r in rs if r["rank"] and r["rank"] <= k) / n, 3) for k in (1, 5, 10)}
    summary[kind].update({f"strict{k}": round(sum(1 for r in rs if r["strict"] and r["strict"] <= k) / n, 3) for k in (1, 10)})
    summary[kind]["sec_median"] = sorted(r["sec"] for r in rs)[n // 2]
print(json.dumps(summary, ensure_ascii=False))
json.dump({"summary": summary, "results": res}, open(a.out, "w"), ensure_ascii=False, indent=1)
