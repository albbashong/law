#!/usr/bin/env python3
"""법령/자치법규의 별표 텍스트를 data/<target>/index.sqlite 의 annex 테이블에 모은다.

  scan  - 수집된 본문 XML을 훑어 별표 목록을 적재. 본문에 텍스트가 있으면 그대로 저장,
          없으면 첨부(HWP/HWPX/PDF) 다운로드 대상으로 표시. 별지·서식(양식)은 제외.
  fetch - 대상 첨부를 받아 텍스트만 추출해 저장 (원본 파일은 저장하지 않음)
  all   - scan 후 fetch (기본값)

사용: .venv/bin/python scripts/fetch_annex.py --target ordin
"""
import argparse
import gzip
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from annex_text import extract

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://www.law.go.kr"
MIN_TEXT = 50  # 본문 XML의 별표내용이 이보다 짧으면 첨부에서 받는다
# 첨부 링크 필드 (법령은 HWP 링크, 자치법규는 첨부파일명에 전체 URL)
LINK_FIELD = {"law": "별표서식파일링크", "ordin": "별표첨부파일명"}

PENDING, DONE, FAILED = 0, 1, 2


def is_form(gubun, title):
    if gubun in ("별지", "서식") and not re.match(r"\W*별표", title):
        return True
    return bool(re.search(r"별지|서식", title[:25])) and not re.match(r"\W*별표", title)


def open_db(target):
    db = sqlite3.connect(ROOT / "data" / target / "index.sqlite", check_same_thread=False)
    db.execute("""CREATE TABLE IF NOT EXISTS annex (
        mst TEXT, no TEXT, branch TEXT, gubun TEXT, title TEXT, url TEXT,
        source TEXT, text TEXT, status INTEGER, error TEXT,
        PRIMARY KEY (mst, no, branch))""")
    db.commit()
    return db


def do_scan(db, target):
    done = {r[0] for r in db.execute("SELECT DISTINCT mst FROM annex")}
    msts = [r[0] for r in db.execute(f"SELECT mst FROM {target} WHERE fetched=1")]
    todo = [m for m in msts if m not in done]
    print(f"[scan] 대상 문서 {len(todo)}건 (이미 스캔 {len(done)}건)", flush=True)
    rows, forms = [], 0
    for i, mst in enumerate(todo, 1):
        path = ROOT / "data" / target / "xml" / mst[:3] / f"{mst}.xml.gz"
        root = ET.fromstring(gzip.open(path).read())
        units = list(root.iter("별표단위"))
        listed = [b for b in units if not is_form(b.findtext("별표구분") or "", (b.findtext("별표제목") or "").strip())]
        body = "".join(t for el in root if el.tag != "별표" for t in el.itertext())
        if not listed and re.search(r"별표\s*\d+", body):
            listed = units      # 본문이 별표를 인용하는데 별표가 없다 — 별표를 서식으로 올린 곳이므로 서식도 받는다
        forms += len(units) - len(listed)
        for b in listed:
            gubun = b.findtext("별표구분") or ""
            title = (b.findtext("별표제목") or "").strip()
            text = "".join(x.text or "" for x in b.iter("별표내용")).strip()
            url = (b.findtext(LINK_FIELD[target]) or "").strip()
            if url.startswith("/"):
                url = SITE + url
            url = url.replace("http://", "https://", 1)
            if len(text) >= MIN_TEXT:
                rows.append((mst, b.findtext("별표번호"), b.findtext("별표가지번호"), gubun,
                             title, url, "xml", text, DONE))
            elif url:
                rows.append((mst, b.findtext("별표번호"), b.findtext("별표가지번호"), gubun,
                             title, url, None, None, PENDING))
        if i % 5000 == 0 or i == len(todo):
            db.executemany("""INSERT OR IGNORE INTO annex
                (mst, no, branch, gubun, title, url, source, text, status)
                VALUES (?,?,?,?,?,?,?,?,?)""", rows)
            db.commit()
            rows = []
            print(f"[scan] {i}/{len(todo)} (서식 제외 누적 {forms})", flush=True)
    stat = dict(db.execute("SELECT status, COUNT(*) FROM annex GROUP BY 1").fetchall())
    print(f"[scan] 완료: 본문텍스트 {stat.get(DONE, 0)}, 다운로드 대상 {stat.get(PENDING, 0)}",
          flush=True)


def is_error_page(data: bytes) -> bool:
    """파일 대신 온 오류 페이지인가 (HTML, 또는 alert('잘못된 접근입니다') 스크립트)."""
    head = data[:200].lstrip().lower()
    return not data or head.startswith((b"<!doctype", b"<html", b"<script")) or \
        "잘못된 접근".encode() in data[:300]


def get(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def do_fetch(db, workers, delay):
    todo = db.execute("SELECT mst, no, branch, url FROM annex WHERE status=?",
                      (PENDING,)).fetchall()
    print(f"[fetch] 남은 {len(todo)}건, 동시 {workers}", flush=True)
    lock = threading.Lock()
    ok = bad = 0
    t0 = time.time()

    def task(url):
        data = get(url)
        time.sleep(delay)
        if is_error_page(data):
            raise ValueError("파일 대신 HTML 응답")
        return extract(data)

    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(task, r[3]): r for r in todo}
        for fut in as_completed(futs):
            mst, no, branch, _ = futs[fut]
            with lock:
                try:
                    kind, text = fut.result()
                    db.execute("UPDATE annex SET source=?, text=?, status=?, error=NULL "
                               "WHERE mst=? AND no=? AND branch=?",
                               (kind, text, DONE, mst, no, branch))
                    ok += 1
                except Exception as e:
                    db.execute("UPDATE annex SET status=?, error=? "
                               "WHERE mst=? AND no=? AND branch=?",
                               (FAILED, f"{type(e).__name__}: {e}"[:300], mst, no, branch))
                    bad += 1
                if (ok + bad) % 500 == 0:
                    db.commit()
                    rate = (ok + bad) / (time.time() - t0)
                    left = (len(todo) - ok - bad) / rate / 3600 if rate else 0
                    print(f"[fetch] {ok + bad}/{len(todo)} (오류 {bad}), {rate:.1f}건/s, "
                          f"남은 약 {left:.1f}시간", flush=True)
    db.commit()
    print(f"[fetch] 완료: 성공 {ok}, 오류 {bad}", flush=True)


def parse_xml(data: bytes):
    """API 응답에 섞여 오는 HTML 엔티티(&nbsp; 등)를 지우고 파싱한다."""
    data = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)\w+;", b" ", data)
    return ET.fromstring(data)


def _clean_title(title: str) -> str:
    t = re.sub(r"<[^>]+>", "", title or "")
    t = re.sub(r"^[\[【(]?\s*별표\s*[\d의\s]*[\]】)]?", "", t).strip()
    return re.sub(r"\s+", " ", t)[:40]


def do_relink(db, workers):
    """본문 XML의 첨부 링크가 낡아 '잘못된 접근' 이 오는 별표를 별표·서식 API(ordinbyl)의 최신 링크로 바꾼다."""
    from fetch_lawdata import BASE, load_oc
    oc = load_oc()
    db.execute("UPDATE annex SET status=?, error='낡은 첨부 링크', text=NULL, source=NULL "
               "WHERE text LIKE '%잘못된 접근%'", (FAILED,))
    db.commit()
    rows = db.execute("SELECT mst, no, branch, title FROM annex WHERE status=?", (FAILED,)).fetchall()
    print(f"[relink] 대상 {len(rows)}건", flush=True)
    lock = threading.Lock()
    found = 0

    def task(row):
        mst, no, branch, title = row
        full = _clean_title(title)
        if len(full) < 2:
            return None
        want = f"{no}{branch or '00'}"
        # 긴 제목은 검색이 안 되는 경우가 있어 짧은 검색어로 한 번 더
        for q in dict.fromkeys([full, full[:15], re.split(r"[\s(]", full)[0]]):
            if len(q) < 2:
                continue
            for page in (1, 2, 3):
                url = f"{BASE}/lawSearch.do?" + urllib.parse.urlencode(
                    {"OC": oc, "target": "ordinbyl", "type": "XML", "display": 100, "page": page, "query": q})
                items = parse_xml(get(url)).findall("ordinbyl")
                for b in items:
                    if b.findtext("관련자치법규일련번호") == mst and b.findtext("별표번호") == want:
                        return SITE + b.findtext("별표서식파일링크")
                if len(items) < 100:
                    break
        return None

    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(task, r): r for r in rows}
        for i, fut in enumerate(as_completed(futs), 1):
            mst, no, branch, _ = futs[fut]
            try:
                link = fut.result()
            except Exception as e:
                link = None
                print(f"[relink] {mst}/{no} 오류: {e}", flush=True)
            if link:
                with lock:
                    db.execute("UPDATE annex SET url=?, status=?, error=NULL WHERE mst=? AND no=? AND branch=?",
                               (link, PENDING, mst, no, branch))
                    found += 1
            if i % 500 == 0:
                db.commit()
                print(f"[relink] {i}/{len(rows)} (새 링크 {found})", flush=True)
    db.commit()
    print(f"[relink] 완료: 새 링크 {found}/{len(rows)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="all", choices=["scan", "fetch", "all", "relink"])
    ap.add_argument("--target", required=True, choices=["law", "ordin"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--retry-failed", action="store_true", help="실패 건을 다시 대상으로")
    ap.add_argument("--retry-empty", action="store_true", help="받았지만 텍스트가 빈 첨부를 다시")
    args = ap.parse_args()
    db = open_db(args.target)
    if args.retry_empty:    # 첨부를 받았는데 텍스트가 비었던 것(추출기 개선 뒤 다시)
        db.execute("UPDATE annex SET status=? WHERE status=? AND source IS NOT NULL AND source!='xml'"
                   " AND COALESCE(length(trim(text)), 0)=0", (PENDING, DONE))
        db.commit()
    if args.retry_failed:
        db.execute("UPDATE annex SET status=? WHERE status=?", (PENDING, FAILED))
        db.commit()
    if args.step in ("scan", "all"):
        do_scan(db, args.target)
    if args.step == "relink":
        do_relink(db, args.workers)
    if args.step in ("fetch", "all", "relink"):
        do_fetch(db, args.workers, args.delay)


if __name__ == "__main__":
    main()
