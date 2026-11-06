"""검색 평가 묶음을 DB 에서 자동으로 만든다 — 손으로 고른 사례가 아니다.

법령(법률·시행령·시행규칙) 조문 중 제목이 드문 것(같은 제목이 법령 조문에 몇 번 안 나오는 것)을 무작위로 뽑아,
  clean: 제목 그대로
  noisy: 제목 + 조문 제목에 가장 흔한 낱말 중 무작위 2~3개 (모델이 낸 "건축신고 대상 면적 기준" 꼴)
를 검색어로, 그 조문을 정답으로 둔다. 정답과 제목이 같은 다른 조문(조례의 같은 제목 등)도 맞은 것으로 센다.
"""
import argparse, collections, json, random, re, sqlite3

ap = argparse.ArgumentParser()
ap.add_argument("--db", default="/home/ubuntu/permit/law/data/lawdb.sqlite")
ap.add_argument("--n", type=int, default=300)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--out", required=True)
a = ap.parse_args()
c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
rows = c.execute("SELECT u.title, d.name, u.label FROM unit u JOIN doc d ON d.doc_id=u.doc_id "
                 "WHERE d.source='law' AND u.path NOT LIKE '%/%' AND u.title IS NOT NULL AND length(u.title) >= 3").fetchall()
norm = lambda t: re.sub(r"\s+", " ", t).strip()
df = collections.Counter(norm(t) for t, _, _ in rows)
words = collections.Counter(w for t in df for w in set(re.findall(r"[가-힣]{2,}", t)))
common = [w for w, _ in words.most_common(60)]
pool = [(norm(t), law, art) for t, law, art in rows if df[norm(t)] <= 3 and "삭제" not in t]
rnd = random.Random(a.seed)
picked = rnd.sample(pool, a.n)
out = []
for title, law, art in picked:
    noise = [w for w in rnd.sample(common, 4) if w not in title][:rnd.choice([2, 3])]
    out.append({"title": title, "law": law, "article": art, "clean": title,
                "noisy": " ".join([title] + noise)})
json.dump({"common": common, "cases": out}, open(a.out, "w"), ensure_ascii=False, indent=1)
print(len(pool), "후보 중", len(out), "개; 흔한 낱말 앞부분:", common[:15])
