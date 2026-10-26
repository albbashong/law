"""lawdb.sqlite 읽기 전용 접근. 테이블 정의는 indexer/build.py."""
import re
import sqlite3
import threading
from pathlib import Path


class StoreUnavailable(RuntimeError):
    pass


def nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        if not path.exists():
            raise StoreUnavailable(f"법령 DB 파일이 없습니다: {path}")
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._tix, self._tix_lock, self._df, self._vocab = None, threading.Lock(), {}, False

    def _all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def _one(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    # 문서
    def doc(self, doc_id):
        return self._one("SELECT * FROM doc WHERE doc_id=?", (doc_id,))

    def docs_by_key(self, key):
        return self._all("SELECT d.* FROM alias a JOIN doc d ON d.doc_id=a.doc_id WHERE a.name_key=?"
                         " ORDER BY d.source, length(d.name)", (key,))

    def aliases(self, doc_id):
        """문서의 다른 이름(약칭) — 공백 뺀 꼴. 정식 이름은 뺀다."""
        return [r["name_key"] for r in self._all(
            "SELECT a.name_key FROM alias a JOIN doc d ON d.doc_id=a.doc_id WHERE a.doc_id=? AND a.name_key<>d.name_key"
            " ORDER BY length(a.name_key)", (doc_id,))]

    def docs_like(self, key, limit=8):
        return self._all("SELECT * FROM doc WHERE name_key LIKE ? ORDER BY source, length(name) LIMIT ?",
                         (f"%{key}%", limit))

    def doc_named(self, name):
        return self._one("SELECT * FROM doc WHERE name=? ORDER BY source LIMIT 1", (name,))

    # 조문
    def unit(self, doc_id, path):
        return self._one("SELECT * FROM unit WHERE doc_id=? AND path=?", (doc_id, path))

    def children(self, doc_id, path):
        return self._all("SELECT * FROM unit WHERE doc_id=? AND parent_path=? ORDER BY unit_id",
                         (doc_id, path))

    def units_with_prefix(self, doc_id, prefix):
        return self._all("SELECT * FROM unit WHERE doc_id=? AND (path=? OR path LIKE ?) ORDER BY unit_id",
                         (doc_id, prefix, prefix + "/%"))

    # 별표
    def annex(self, doc_id, no):
        return self._one("SELECT * FROM annex WHERE doc_id=? AND no=?", (doc_id, no))

    def annexes(self, doc_id):
        return self._all("SELECT annex_id, no, title, text IS NOT NULL AS has_text FROM annex"
                         " WHERE doc_id=? ORDER BY annex_id", (doc_id,))

    def annex_items(self, annex_id):
        return self._all("SELECT * FROM annex_item WHERE annex_id=? ORDER BY item_id", (annex_id,))

    def annex_item(self, doc_id, no, path):
        return self._one("SELECT i.* FROM annex_item i JOIN annex a ON a.annex_id=i.annex_id"
                         " WHERE a.doc_id=? AND a.no=? AND i.path=?", (doc_id, no, path))

    # 연결
    def links_from(self, doc_id, path):
        return self._all("SELECT * FROM link WHERE from_doc=? AND from_path=?", (doc_id, path))

    def links_from_prefix(self, doc_id, prefix):
        return self._all("SELECT * FROM link WHERE from_doc=? AND (from_path=? OR from_path LIKE ?)",
                         (doc_id, prefix, prefix + "/%"))

    def links_to_prefix(self, doc_id, prefix, from_doc=None):
        sql = "SELECT * FROM link WHERE to_doc=? AND (to_path=? OR to_path LIKE ?)"
        args = [doc_id, prefix, prefix + "/%"]
        if from_doc is not None:
            sql += " AND from_doc=?"
            args.append(from_doc)
        return self._all(sql, args)

    def cited_docs(self, doc_id) -> set:
        """이 문서가 본문에서 실제로 인용하는 다른 문서들."""
        return {r[0] for r in self.db.execute(
            "SELECT DISTINCT to_doc FROM link WHERE from_doc=? AND kind='인용' AND to_doc<>?", (doc_id, doc_id))}

    def doc_links(self, doc_id, kind="체계"):
        return self._all("SELECT * FROM link WHERE (from_doc=? OR to_doc=?) AND kind=? AND from_path IS NULL",
                         (doc_id, doc_id, kind))

    # 지역·용도지역
    def region_plan(self, region):
        return self._all("SELECT r.region, r.sido, d.doc_id, d.name, d.org FROM region_plan r"
                         " JOIN doc d ON d.doc_id=r.doc_id WHERE r.region=?", (region,))

    def sido_known(self, sido) -> bool:
        return self._one("SELECT 1 FROM region_plan WHERE sido=? LIMIT 1", (sido,)) is not None

    def zone(self, zone):
        return self._one("SELECT * FROM zone_map WHERE zone=?", (nospace(zone),))

    def ordin_docs_of(self, org_doc_id):
        org = self.doc(org_doc_id)["org"]
        return self._all("SELECT * FROM doc WHERE source='ordin' AND org=?", (org,))

    # 검색 (trigram 은 3글자 이상 필요)
    def search_units(self, q, doc_ids=None, source=None, limit=8):
        """일치도(bm25) 순 — 순서 없이 자르면 저장 순서(법령명 가나다)가 곧 순위가 된다."""
        sql = ("SELECT u.*, d.name AS law, d.source, f.rank AS score FROM fts_unit f JOIN unit u ON u.unit_id=f.rowid"
               " JOIN doc d ON d.doc_id=u.doc_id WHERE fts_unit MATCH ?")
        args = [q]
        if doc_ids:
            sql += f" AND u.doc_id IN ({','.join('?' * len(doc_ids))})"
            args += list(doc_ids)
        if source:
            sql += " AND d.source=?"
            args.append(source)
        return self._all(sql + " ORDER BY f.rank LIMIT ?", args + [limit])

    def search_annex(self, q, doc_ids=None, source=None, limit=8):
        sql = ("SELECT i.*, a.no, a.title AS annex_title, a.doc_id, d.name AS law, d.source"
               " FROM fts_annex f JOIN annex_item i ON i.item_id=f.rowid"
               " JOIN annex a ON a.annex_id=i.annex_id JOIN doc d ON d.doc_id=a.doc_id"
               " WHERE fts_annex MATCH ?")
        args = [q]
        if doc_ids:
            sql += f" AND a.doc_id IN ({','.join('?' * len(doc_ids))})"
            args += list(doc_ids)
        if source:
            sql += " AND d.source=?"
            args.append(source)
        return self._all(sql + " ORDER BY f.rank LIMIT ?", args + [limit])

    # 제목 검색 — trigram 으로 못 찾는 2글자 법령 용어(식비·선물·전용 등)를 조문·별표 제목에서 찾는다.
    # 제목만 모은 작은 표를 처음 쓸 때 메모리에 만든다(DB 파일은 읽기 전용 그대로).
    def _titles(self):
        with self._tix_lock:
            if self._tix is None:
                mem = sqlite3.connect(":memory:", check_same_thread=False)
                mem.execute("CREATE TABLE t(kind TEXT, ref INT, doc INT, src TEXT, s TEXT)")
                src = {d: so for d, so in self.db.execute("SELECT doc_id, source FROM doc")}
                rows = self.db.execute("SELECT 'u', unit_id, doc_id, replace(title,' ','') FROM unit"
                                       " WHERE title IS NOT NULL AND title!='' AND path NOT LIKE '%/%'").fetchall()
                rows += self.db.execute("SELECT 'a', annex_id, doc_id, replace(coalesce(title,''),' ','') FROM annex"
                                        " WHERE title IS NOT NULL AND title!=''").fetchall()
                mem.executemany("INSERT INTO t VALUES (?,?,?,?,?)", [(k, r, d, src.get(d), t) for k, r, d, t in rows])
                self._tix = mem
            return self._tix

    def title_df(self, word):
        """그 낱말이 든 제목 수 — 드문 낱말일수록 제목 점수를 크게 준다."""
        if word not in self._df:
            self._df[word] = self._titles().execute("SELECT count(*) FROM t WHERE instr(s, ?) > 0", (word,)).fetchone()[0]
        return self._df[word]

    # 2글자 낱말 — trigram 은 3글자 조각만 색인하므로, 색인 낱말 목록에서 그 낱말로 시작하는 조각을 모아 대신 찾는다.
    def index_grams(self, word, limit=40):
        """2글자면 색인에 실제로 있는 '그 낱말+한 글자' 조각들(많이 나오는 순), 아니면 [word]."""
        if len(word) != 2:
            return [word]
        with self._tix_lock:
            if not self._vocab:
                for t in ("fts_unit", "fts_annex"):
                    self.db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.{t}_vocab USING fts5vocab(main, {t}, row)")
                self._vocab = True
        end = word[:-1] + chr(ord(word[-1]) + 1)
        rows = self.db.execute("SELECT term, sum(doc) n FROM (SELECT term, doc FROM temp.fts_unit_vocab WHERE term>=? AND term<?"
                               " UNION ALL SELECT term, doc FROM temp.fts_annex_vocab WHERE term>=? AND term<?)"
                               " GROUP BY term ORDER BY n DESC LIMIT ?", (word, end, word, end, limit)).fetchall()
        return [r[0] for r in rows if '"' not in r[0]]

    def unit_count(self):
        if "units" not in self._df:
            self._df["units"] = self.db.execute("SELECT count(*) FROM unit").fetchone()[0]
        return self._df["units"]

    def body_df(self, expr):
        """그 검색식에 맞는 조문 수 — 흔한 낱말부터 빼고 다시 찾을 때 쓴다."""
        key = ("body", expr)
        if key not in self._df:
            self._df[key] = self.db.execute("SELECT count(*) FROM fts_unit WHERE fts_unit MATCH ?", (expr,)).fetchone()[0]
        return self._df[key]

    def search_titles(self, words, doc_ids=None, source=None, limit=40):
        """제목에 든 검색어의 드묾(log N/df) 합 순, 같으면 법령 먼저 — [{kind, ref, doc_id, src, score}]."""
        import math
        words = [w for w in dict.fromkeys(words) if w]
        if not words:
            return []
        n = self._titles().execute("SELECT count(*) FROM t").fetchone()[0]
        weights = [round(math.log((n + 1) / (self.title_df(w) + 1)), 3) for w in words]
        score = "+".join("(instr(s, ?) > 0) * ?" for _ in words)
        sql = f"SELECT kind, ref, doc AS doc_id, src, {score} AS score FROM t WHERE score > 0"
        args = [x for pair in zip(words, weights) for x in pair]
        if doc_ids:
            sql += f" AND doc IN ({','.join('?' * len(doc_ids))})"
            args += list(doc_ids)
        if source:
            sql += " AND src=?"
            args.append(source)
        cur = self._titles().execute(sql + " ORDER BY score DESC, src='law' DESC, length(s) LIMIT ?", args + [limit])
        return [dict(zip(("kind", "ref", "doc_id", "src", "score"), r)) for r in cur]

    def docs_named_in(self, text):
        """글 안에 이름(공백 무시)이 통째로 든 법령(세 글자 이상: 건축법·농지법) — 긴 이름 먼저."""
        return self._all("SELECT * FROM doc WHERE length(name_key) >= 3 AND instr(?, name_key) > 0"
                         " ORDER BY source='law' DESC, length(name_key) DESC LIMIT 5", (text,))

    def search_text_in(self, doc_ids, words, limit=8):
        """정해진 법령 안에서 본문·별표 텍스트에 낱말이 많이 든 조문·별표 — [{kind, ref, doc_id, score}]."""
        if not doc_ids or not words:
            return []
        marks = ",".join("?" * len(doc_ids))
        score = "+".join("(instr(replace(text,' ',''), ?) > 0)" for _ in words)
        titled = "+".join("(instr(replace(COALESCE(title,''),' ',''), ?) > 0)" for _ in words)   # 제목에 든 낱말은 한 번 더
        rows = self._all(f"SELECT 'u' AS kind, unit_id AS ref, doc_id, {score} + {titled} AS score FROM unit"
                         f" WHERE doc_id IN ({marks}) AND path NOT LIKE '%/%' AND {score} > 0",
                         list(words) * 2 + list(doc_ids) + list(words))
        rows += self._all(f"SELECT 'a' AS kind, annex_id AS ref, doc_id, {score} AS score FROM annex"
                          f" WHERE doc_id IN ({marks}) AND score > 0", list(words) + list(doc_ids))
        return sorted(rows, key=lambda r: -r["score"])[:limit]

    def health(self):
        return {"docs": self.db.execute("SELECT count(*) FROM doc").fetchone()[0],
                "built_at": (self._one("SELECT v FROM meta WHERE k='built_at'") or {}).get("v")}
