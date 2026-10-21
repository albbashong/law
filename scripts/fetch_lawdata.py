#!/usr/bin/env python3
"""국가법령정보 Open API로 법령/자치법규 본문(XML)을 수집한다.

사용법:
  python3 scripts/fetch_lawdata.py --target law     # 국가 법령 (법률·시행령·시행규칙 등)
  python3 scripts/fetch_lawdata.py --target ordin   # 자치법규 (조례·규칙)

단계 (위치 인자, 기본 all):
  list  - 목록 API(lawSearch)를 페이지 단위로 돌며 data/<target>/index.sqlite에 적재
  fetch - 본문 API(lawService)로 미수집 건을 받아 data/<target>/xml/<앞3자리>/<MST>.xml.gz 로 저장
  all   - list 후 fetch

중단 후 다시 실행하면 이어서 진행한다. 목록을 처음부터 다시 훑으려면 --relist.
HWP/PDF 첨부파일은 받지 않는다 (첨부 링크는 본문 XML에 남아 있음).
"""
import argparse
import gzip
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://www.law.go.kr/DRF"
ROOT = Path(__file__).resolve().parent.parent
PAGE_SIZE = 100

# 목록 XML 필드 → DB 컬럼 매핑, 본문 응답 확인용 시작 태그
TARGETS = {
    "law": {
        "fields": {"mst": "법령일련번호", "item_id": "법령ID", "name": "법령명한글",
                   "org": "소관부처명", "kind": "법령구분명", "field": "법령약칭명",
                   "promul_date": "공포일자", "effect_date": "시행일자",
                   "rev_type": "제개정구분명", "status": "현행연혁코드"},
        "marker": "<법령".encode(),
    },
    "ordin": {
        "fields": {"mst": "자치법규일련번호", "item_id": "자치법규ID", "name": "자치법규명",
                   "org": "지자체기관명", "kind": "자치법규종류", "field": "자치법규분야명",
                   "promul_date": "공포일자", "effect_date": "시행일자",
                   "rev_type": "제개정구분명", "status": None},
        "marker": b"<LawService",
    },
}
COLS = ["mst", "item_id", "name", "org", "kind", "field",
        "promul_date", "effect_date", "rev_type", "status"]


def load_oc():
    oc = os.environ.get("LAW_API_OC")
    if oc:
        return oc
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("LAW_API_OC="):
                return line.split("=", 1)[1].strip()
    sys.exit("LAW_API_OC가 없습니다 (.env 또는 환경변수)")


def get(url, retries=5):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


class Store:
    def __init__(self, target):
        self.target = target
        self.dir = ROOT / "data" / target
        self.xml_dir = self.dir / "xml"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "index.sqlite", check_same_thread=False)
        self.db.execute(f"""CREATE TABLE IF NOT EXISTS {target} (
            mst TEXT PRIMARY KEY, item_id TEXT, name TEXT, org TEXT, kind TEXT,
            field TEXT, promul_date TEXT, effect_date TEXT, rev_type TEXT, status TEXT,
            fetched INTEGER DEFAULT 0, error TEXT)""")
        # 구버전 스키마(ordin_id 컬럼, status 없음) 호환
        cols = [r[1] for r in self.db.execute(f"PRAGMA table_info({target})")]
        if "ordin_id" in cols:
            self.db.execute(f"ALTER TABLE {target} RENAME COLUMN ordin_id TO item_id")
        if "status" not in cols:
            self.db.execute(f"ALTER TABLE {target} ADD COLUMN status TEXT")
        # 갱신: seen = 이 행이 마지막으로 보인 목록 회차, superseded = 마지막 전체 목록에 없었음(개정·폐지된 판)
        if "seen" not in cols:
            self.db.execute(f"ALTER TABLE {target} ADD COLUMN seen INTEGER")
        if "superseded" not in cols:
            self.db.execute(f"ALTER TABLE {target} ADD COLUMN superseded INTEGER DEFAULT 0")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.db.commit()

    def meta(self, k, v=None):
        if v is None:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return row[0] if row else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
        self.db.commit()

    def xml_path(self, mst):
        return self.xml_dir / mst[:3] / f"{mst}.xml.gz"


def list_page(oc, target, page):
    q = urllib.parse.urlencode({"OC": oc, "target": target, "type": "XML",
                                "display": PAGE_SIZE, "page": page})
    root = ET.fromstring(get(f"{BASE}/lawSearch.do?{q}"))
    total = int(root.findtext("totalCnt") or 0)
    fields = TARGETS[target]["fields"]
    rows = [tuple(l.findtext(fields[c]) if fields[c] else None for c in COLS)
            for l in root.findall("law")]
    return total, rows


def do_list(st, oc, workers, relist):
    """목록을 페이지 단위로 적재한다. 처음부터 훑는 회차마다 번호(list_stamp)를 매기고, 끝까지 훑으면 그 회차에
    보이지 않은 판을 superseded 로 표시한다(중간에 끊기면 표시하지 않고 다음 실행이 이어서 훑는다)."""
    if relist:
        st.meta("list_done_page", 0)
    total, _ = list_page(oc, st.target, 1)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = int(st.meta("list_done_page") or 0)
    if start == 0:
        st.meta("list_stamp", int(st.meta("list_stamp") or 0) + 1)
    stamp = int(st.meta("list_stamp"))
    print(f"[list] 총 {total}건, {pages}페이지 (완료 {start}페이지부터 재개, 회차 {stamp})", flush=True)
    sql = (f"INSERT INTO {st.target} ({','.join(COLS)}, seen) VALUES ({','.join('?' * len(COLS))}, {stamp}) "
           "ON CONFLICT(mst) DO UPDATE SET seen=excluded.seen")
    for chunk in range(start + 1, pages + 1, workers * 5):
        batch = range(chunk, min(chunk + workers * 5, pages + 1))
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(lambda p: list_page(oc, st.target, p)[1], batch))
        for rows in results:
            st.db.executemany(sql, [r for r in rows if r[0]])
        st.meta("list_done_page", batch[-1])
        n = st.db.execute(f"SELECT COUNT(*) FROM {st.target}").fetchone()[0]
        print(f"[list] {batch[-1]}/{pages}페이지, 누적 {n}건", flush=True)
    st.meta("list_total", total)
    st.db.execute(f"UPDATE {st.target} SET superseded = (seen IS NULL OR seen != ?)", (stamp,))
    st.db.commit()
    gone = st.db.execute(f"SELECT COUNT(*) FROM {st.target} WHERE superseded=1").fetchone()[0]
    print(f"[list] 이번 목록에 없는 판(개정·폐지) {gone}건 — 색인에서 뺀다", flush=True)


def fetch_one(st, oc, mst):
    q = urllib.parse.urlencode({"OC": oc, "target": st.target, "MST": mst, "type": "XML"})
    body = get(f"{BASE}/lawService.do?{q}")
    if TARGETS[st.target]["marker"] not in body[:500]:
        raise ValueError(body[:200].decode("utf-8", "replace"))
    path = st.xml_path(mst)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(body)
    tmp.rename(path)


def do_fetch(st, oc, workers, delay):
    todo = [r[0] for r in st.db.execute(
        f"SELECT mst FROM {st.target} WHERE fetched=0 ORDER BY mst")]
    print(f"[fetch] 남은 {len(todo)}건, 동시 {workers}", flush=True)
    lock = threading.Lock()
    done = errors = 0
    t0 = time.time()

    def task(mst):
        fetch_one(st, oc, mst)
        time.sleep(delay)

    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(task, m): m for m in todo}
        for fut in as_completed(futs):
            mst = futs[fut]
            err = fut.exception()
            with lock:
                if err:
                    errors += 1
                    st.db.execute(f"UPDATE {st.target} SET error=? WHERE mst=?",
                                  (str(err)[:300], mst))
                else:
                    done += 1
                    st.db.execute(f"UPDATE {st.target} SET fetched=1, error=NULL WHERE mst=?",
                                  (mst,))
                if (done + errors) % 500 == 0:
                    st.db.commit()
                    rate = (done + errors) / (time.time() - t0)
                    left = (len(todo) - done - errors) / rate / 3600 if rate else 0
                    print(f"[fetch] {done + errors}/{len(todo)} (오류 {errors}), "
                          f"{rate:.1f}건/s, 남은 약 {left:.1f}시간", flush=True)
    st.db.commit()
    print(f"[fetch] 완료: 성공 {done}, 오류 {errors}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="all", choices=["list", "fetch", "all"])
    ap.add_argument("--target", required=True, choices=list(TARGETS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.1, help="요청 후 대기(초, 워커당)")
    ap.add_argument("--relist", action="store_true", help="목록을 처음부터 다시 수집")
    args = ap.parse_args()
    oc = load_oc()
    st = Store(args.target)
    if args.step in ("list", "all"):
        do_list(st, oc, args.workers, args.relist)
    if args.step in ("fetch", "all"):
        do_fetch(st, oc, args.workers, args.delay)


if __name__ == "__main__":
    main()
