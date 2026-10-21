#!/usr/bin/env python3
"""법령 사이 관계 데이터를 data/links/*.jsonl 로 수집한다.

  stmd      - 법령체계도(lsStmd): 법령마다 XML 원문 그대로       → stmd.jsonl
  delegated - 위임조문(lsDelegated): 법률·대통령령의 위임 행     → delegated.jsonl
  ordjo     - 법령-자치법규 조문 연계(lnkLsOrdJo): 전 페이지     → ordjo.jsonl
  all       - 셋 다 (기본값)

다시 실행하면 이미 받은 키는 건너뛴다.
"""
import argparse
import json
import sqlite3
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fetch_lawdata import BASE, ROOT, get, load_oc

OUT = ROOT / "data" / "links"
LAW_DB = ROOT / "data" / "law" / "index.sqlite"


def done_keys(path, key):
    if not path.exists():
        return set()
    with path.open() as f:
        return {json.loads(line)[key] for line in f if line.strip()}


class Writer:
    def __init__(self, path):
        self.f = path.open("a")
        self.lock = threading.Lock()

    def write(self, rows):
        with self.lock:
            for r in rows:
                self.f.write(json.dumps(r, ensure_ascii=False) + "\n")
            self.f.flush()


def run_pool(items, fn, workers, label):
    n = len(items)
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(fn, items), 1):
            if i % 500 == 0 or i == n:
                print(f"[{label}] {i}/{n}", flush=True)


def laws():
    db = sqlite3.connect(LAW_DB)
    return db.execute("SELECT mst, item_id, kind FROM law").fetchall()


def do_stmd(oc, workers):
    path = OUT / "stmd.jsonl"
    seen = done_keys(path, "mst")
    todo = [m for m, _, _ in laws() if m not in seen]
    print(f"[stmd] 남은 {len(todo)}건", flush=True)
    w = Writer(path)

    def fn(mst):
        q = urllib.parse.urlencode({"OC": oc, "target": "lsStmd", "MST": mst, "type": "XML"})
        try:
            w.write([{"mst": mst, "xml": get(f"{BASE}/lawService.do?{q}").decode("utf-8")}])
        except Exception as e:
            print(f"[stmd] {mst} 실패: {e}", flush=True)

    run_pool(todo, fn, workers, "stmd")


def _as_list(v):
    return v if isinstance(v, list) else ([v] if v else [])


def do_delegated(oc, workers):
    path = OUT / "delegated.jsonl"
    seen = done_keys(path, "law_id")
    todo = sorted({lid for _, lid, kind in laws() if kind in ("법률", "대통령령")} - seen)
    print(f"[delegated] 남은 {len(todo)}건", flush=True)
    w = Writer(path)

    def fn(law_id):
        q = urllib.parse.urlencode({"OC": oc, "target": "lsDelegated", "ID": law_id,
                                    "type": "JSON"})
        try:
            data = json.loads(get(f"{BASE}/lawService.do?{q}"))
        except Exception as e:
            print(f"[delegated] {law_id} 실패: {e}", flush=True)
            return
        law = (data.get("lsDelegated") or {}).get("법령") or {}
        rows = []
        for item in _as_list(law.get("위임조문정보")):
            src = item.get("조정보") or {}
            for d in _as_list(item.get("위임정보")):
                for jo in _as_list(d.get("위임법령조문정보")):
                    rows.append({"law_id": law_id, "src_jo": src.get("조문번호"),
                                 "src_path": jo.get("조항호목"), "dst_mst": d.get("위임법령일련번호"),
                                 "dst_name": d.get("위임법령제목"), "kind": d.get("위임구분"),
                                 "dst_jo": jo.get("위임법령조문번호"), "line": jo.get("라인텍스트")})
        # 위임이 없는 법령도 완료 표시를 남긴다
        w.write(rows or [{"law_id": law_id, "src_jo": None}])

    run_pool(todo, fn, workers, "delegated")


def do_ordjo(oc, workers, fresh=False):
    """fresh: 갱신용 — 페이지가 밀리므로 새 파일(ordjo.jsonl.new)에 처음부터 받아, 다 받으면 바꿔 끼운다.
    중간에 끊기면 .new 에 이어서 받는다."""
    final = OUT / "ordjo.jsonl"
    path = OUT / "ordjo.jsonl.new" if fresh else final
    seen = done_keys(path, "page")
    q = urllib.parse.urlencode({"OC": oc, "target": "lnkLsOrdJo", "type": "XML", "display": 1})
    total = int(ET.fromstring(get(f"{BASE}/lawSearch.do?{q}")).findtext("totalCnt"))
    pages = [p for p in range(1, (total + 99) // 100 + 1) if p not in seen]
    print(f"[ordjo] 총 {total}건, 남은 {len(pages)}페이지", flush=True)
    w = Writer(path)
    fields = {"법령명한글": "law_name", "법령ID": "law_id", "법령조번호": "law_jo",
              "자치법규일련번호": "ordin_mst", "자치법규명": "ordin_name", "자치법규조번호": "ordin_jo"}

    def fn(page):
        q = urllib.parse.urlencode({"OC": oc, "target": "lnkLsOrdJo", "type": "XML",
                                    "display": 100, "page": page})
        try:
            root = ET.fromstring(get(f"{BASE}/lawSearch.do?{q}"))
        except Exception as e:
            print(f"[ordjo] {page} 실패: {e}", flush=True)
            return
        w.write([{"page": page, **{v: l.findtext(k) for k, v in fields.items()}}
                 for l in root.findall("law")])

    run_pool(pages, fn, workers, "ordjo")
    w.f.close()
    if fresh:
        got = len(done_keys(path, "page"))
        want = (total + 99) // 100
        if got < want:
            raise SystemExit(f"[ordjo] {want}페이지 중 {got}페이지만 받았습니다 — 다시 실행하면 이어서 받습니다")
        path.replace(final)
        print(f"[ordjo] 새 연계 {total}건으로 교체", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="all", choices=["stmd", "delegated", "ordjo", "all"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--fresh", action="store_true", help="ordjo 를 새 파일에 처음부터 받아 교체(갱신용)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    oc = load_oc()
    for step, fn in (("stmd", do_stmd), ("delegated", do_delegated)):
        if args.step in (step, "all"):
            fn(oc, args.workers)
    if args.step in ("ordjo", "all"):
        do_ordjo(oc, args.workers, fresh=args.fresh)


if __name__ == "__main__":
    main()
