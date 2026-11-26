"""일상어 검색 평가 묶음 — make_set.py 로 뽑은 조문마다, 법을 모르는 사람이 칠 법한 일상어 질문을 모델이 만든다.
조문 제목·법령명 낱말과 법률 용어를 쓰지 않게 하고(검색어와 조문의 말이 다른 경우를 재려는 것), 그래도 겹친 낱말은 overlap 에 적는다.
모델 주소·키는 agent/law_review/.env 에서 읽는다.

  python eval/search/make_daily_set.py eval/search/sets/auto300.json eval/search/sets/daily270.json
"""
import json, re, sqlite3, httpx, concurrent.futures as cf, sys
env = dict(l.split("=", 1) for l in open("/home/ubuntu/permit/law/agent/law_review/.env", encoding="utf-8").read().splitlines()
           if "=" in l and not l.lstrip().startswith("#"))
g = lambda k: env[k].strip().strip('"').strip("'")
BASE, MODEL, KEY = g("VLLM_BASE").rstrip("/"), g("VLLM_MODEL"), g("VLLM_API_KEY")
cases = json.load(open(sys.argv[1]))["cases"]
c = sqlite3.connect("file:/home/ubuntu/permit/law/data/lawdb.sqlite?mode=ro", uri=True)
def text(law, art):
    r = c.execute("select u.text from unit u join doc d using(doc_id) where d.name=? and u.label=? and u.path not like '%/%'", (law, art)).fetchone()
    return (r[0] if r else "")[:1500]
items = [dict(i=i, law=x["law"], article=x["article"], title=x["title"], text=text(x["law"], x["article"])) for i, x in enumerate(cases)]
SYS = ("너는 법을 모르는 일반 시민이다. 주어진 조문이 답이 되는 질문을 하나씩 만든다.\n"
       "- 시민이 검색창에 칠 법한 일상어 문장(15~45자). 상황을 구체적으로.\n"
       "- 조문 제목과 법령명에 나오는 낱말, 조문 번호, 법률 전문용어를 쓰지 말고 쉬운 말로 바꿔 쓴다.\n"
       "- 답은 반드시 그 조문이어야 한다.\n"
       '출력은 JSON 배열만: [{"i": 번호, "q": "질문"}, ...]')
def ask(batch):
    user = "\n\n".join(f"[{b['i']}] {b['law']} {b['article']}({b['title']})\n{b['text']}" for b in batch)
    for _ in range(3):
        try:
            r = httpx.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {KEY}"}, timeout=300,
                           json={"model": MODEL, "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}], "temperature": 0.3})
            s = r.json()["choices"][0]["message"]["content"]
            return json.loads(re.search(r"\[.*\]", s, re.S).group(0))
        except Exception as e:
            err = type(e).__name__
    print("fail batch", batch[0]["i"], err, flush=True); return []
out = {}
with cf.ThreadPoolExecutor(4) as ex:
    for res in ex.map(ask, [items[k:k + 10] for k in range(0, len(items), 10)]):
        for x in res: out[int(x["i"])] = x["q"]
words = lambda s: set(re.findall(r"[가-힣]{2,}", s))
res = []
for i, x in enumerate(cases):
    if i not in out: continue
    q = out[i]; tw = words(x["title"]) | words(x["law"])
    overlap = [w for w in words(q) if any(w in t or t in w for t in tw)]
    res.append({**x, "q": q, "overlap": overlap})
json.dump({"cases": res}, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print(len(res), "made;", sum(1 for r in res if not r["overlap"]), "no overlap")
