"""낱말 색인 — 형태소(kiwi)로 나눈 낱말과 이어진 명사를 붙인 복합어를 조문·별표의 제목·본문 칸에 색인하고 bm25(제목 가중)로 찾는다.

trigram 색인은 세 글자 조각만 보고 낱말을 모른다. 분석기는 "건축신고"를 "건축 + 신고"로 쪼개므로 붙인 "건축신고"도
넣어야 드문 복합어가 흔한 낱말로 흩어지지 않는다. 색인과 검색어는 반드시 같은 words() 로 나눈다 — 그래서 만드는 일도
여기 둔다(scripts/build_words.py). 색인은 lawdb.sqlite 와 따로 둔 파일이다."""
import logging
import multiprocessing as mp
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CONTENT = ("NNG", "NNP", "XR", "SL", "SN", "SH", "VV", "VA")   # 낱말로 남기는 품사(명사·어근·외국어·숫자·한자·용언 어간)
NOUN = ("NNG", "NNP", "XR")                                   # 이어지면 붙여 복합어로도 남기는 품사
BRIDGE = "J"   # 명사 사이에 이 품사(조사) 하나만 끼면 건너뛰어 잇는다 — 제목 "도로의 점용 허가" ↔ 검색어 "도로점용허가"
JOIN_MAX = 3                                                  # 복합어로 붙이는 명사 수 상한
PIECE = 3000   # 긴 본문(별표 표)은 줄 단위로 이만큼씩 나눠 분석한다 — 통째로 넣으면 분석기 메모리가 커져 프로세스가 죽었다

_kiwi = None
_index: dict = {}


def _analyzer():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi(num_workers=-1)
    return _kiwi


def _pieces(text: str):
    buf = ""
    for line in (text or "").splitlines(keepends=True):
        if buf and len(buf) + len(line) > PIECE:
            yield buf
            buf = ""
        buf += line[:PIECE]
    if buf:
        yield buf


def _joined(run: list[str]) -> list[str]:
    return ["".join(run[i:j]) for i in range(len(run)) for j in range(i + 2, min(i + JOIN_MAX + 1, len(run) + 1))]


def words(text: str) -> list[str]:
    out = []
    for piece in _pieces(text):
        run, bridged = [], False
        for t in _analyzer().tokenize(piece):
            tag = t.tag.split("-")[0]
            if tag in NOUN:
                run.append(t.form)
                bridged = False
            elif run and not bridged and tag.startswith(BRIDGE):
                bridged = True     # 다음이 명사면 잇고, 아니면 아래에서 끊는다
            else:
                out += _joined(run)
                run, bridged = [], False
            if tag in CONTENT:
                out.append(t.form)
        out += _joined(run)
    return out


def _open(path: Path):
    """색인 연결 — 파일이 없거나 분석기가 없으면 None(부르는 쪽은 trigram 으로 돌아간다)."""
    key = str(path)
    if key not in _index:
        conn = None
        if Path(path).exists():
            try:
                _analyzer()
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            except Exception as e:  # noqa: BLE001 — 분석기 설치·색인 파일 문제는 검색을 멈추지 않는다
                logger.warning("words index unavailable: %s", e)
        else:
            logger.warning("words index not found: %s", path)
        _index[key] = conn
    return _index[key]


def search(path: Path, query: str, title_weight: float, limit: int) -> list[dict] | None:
    """[{kind: u|a, ref, score}] 를 bm25 순으로(작을수록 앞). 색인을 못 쓰면 None."""
    conn = _open(path)
    if conn is None:
        return None
    toks = list(dict.fromkeys(words(query)))
    if not toks:
        return []
    match = " OR ".join('"' + t.replace('"', "") + '"' for t in toks)
    hits = []
    for kind, table in (("u", "w_unit"), ("a", "w_annex")):
        hits += [{"kind": kind, "ref": r, "score": s} for r, s in conn.execute(
            f"SELECT rowid, bm25({table}, ?, 1.0) AS s FROM {table} WHERE {table} MATCH ? ORDER BY s LIMIT ?",
            (title_weight, match, limit))]
    return sorted(hits, key=lambda h: h["score"])


def _row(r):
    return r[0], " ".join(words(r[1] or "")), " ".join(words(r[2] or ""))


def build_words_index(src: Path, out: Path, procs: int = 8, log=print) -> None:
    """lawdb.sqlite 의 조문(항·호 아래는 조문 본문에 들어 있어 조문 단위만)과 별표를 낱말 색인으로 만든다."""
    db = sqlite3.connect(f"file:{src}?mode=ro", uri=True, check_same_thread=False)   # 풀이 다른 스레드에서 읽는다
    dst = sqlite3.connect(out)
    dst.execute("PRAGMA journal_mode=WAL")   # 중간에 꺼져도 파일이 깨지지 않게
    dst.execute("PRAGMA synchronous=NORMAL")
    jobs = [("w_unit", "SELECT unit_id, title, text FROM unit WHERE path NOT LIKE '%/%'"),
            ("w_annex", "SELECT annex_id, title, text FROM annex")]
    t0 = time.perf_counter()
    pool = mp.Pool(procs) if procs > 1 else None
    try:
        for table, sql in jobs:
            dst.execute(f"DROP TABLE IF EXISTS {table}")
            dst.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(title, body, content='', tokenize='unicode61')")
            rows = pool.imap(_row, db.execute(sql), chunksize=200) if pool else map(_row, db.execute(sql))
            n = 0
            for row in rows:
                dst.execute(f"INSERT INTO {table}(rowid, title, body) VALUES (?,?,?)", row)
                n += 1
                if n % 100000 == 0:
                    dst.commit()
                    log(f"{table} {n} {time.perf_counter() - t0:.0f}s")
            dst.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
            dst.commit()
            log(f"{table} done {n} {time.perf_counter() - t0:.0f}s")
    finally:
        if pool:
            pool.close()
