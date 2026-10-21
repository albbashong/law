"""수집 원본을 data/lawdb.sqlite 하나로 색인한다.

입력: data/{law,ordin}/index.sqlite (목록·별표 텍스트), data/{law,ordin}/xml/*.xml.gz (본문),
      data/links/ordjo.jsonl (법령 조 ↔ 조례 조 연계)
출력: 새 파일을 만든 뒤 os.replace 로 교체한다. 반환값은 품질 보고서(dict).
"""
import gzip
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from indexer.annex_items import split_annex
from indexer.annex_split import normalize_annexes
from indexer.citations import citation_path, find_citations
from indexer.units import law_units, ordin_units

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SCHEMA = """
CREATE TABLE doc(doc_id INTEGER PRIMARY KEY, source TEXT, mst TEXT, law_id TEXT, name TEXT,
  name_key TEXT, short_name TEXT, kind TEXT, org TEXT, effect_date TEXT);
CREATE TABLE unit(unit_id INTEGER PRIMARY KEY, doc_id INT, path TEXT, label TEXT, title TEXT,
  text TEXT, parent_path TEXT, UNIQUE(doc_id, path));
CREATE TABLE annex(annex_id INTEGER PRIMARY KEY, doc_id INT, no TEXT, title TEXT, text TEXT,
  UNIQUE(doc_id, no));
CREATE TABLE annex_item(item_id INTEGER PRIMARY KEY, annex_id INT, path TEXT, label TEXT, text TEXT);
CREATE TABLE link(from_doc INT, from_path TEXT, to_doc INT, to_path TEXT, kind TEXT, evidence TEXT,
  UNIQUE(from_doc, from_path, to_doc, to_path, kind));
CREATE TABLE alias(name_key TEXT, doc_id INT, UNIQUE(name_key, doc_id));
CREATE TABLE zone_map(zone TEXT PRIMARY KEY, item_path TEXT, annex_no TEXT, mode TEXT);
CREATE TABLE region_plan(region TEXT, sido TEXT, doc_id INT, UNIQUE(region, doc_id));
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE fts_unit USING fts5(body, content='', tokenize='trigram');
CREATE VIRTUAL TABLE fts_annex USING fts5(body, content='', tokenize='trigram');
"""
POST_INDEX = """
CREATE INDEX ix_doc_key ON doc(name_key);
CREATE INDEX ix_doc_lawid ON doc(law_id);
CREATE INDEX ix_unit_parent ON unit(doc_id, parent_path);
CREATE INDEX ix_item ON annex_item(annex_id, path);
CREATE INDEX ix_link_from ON link(from_doc, from_path);
CREATE INDEX ix_link_to ON link(to_doc, to_path);
CREATE INDEX ix_alias ON alias(name_key);
CREATE INDEX ix_region ON region_plan(region);
"""
MANUAL_ALIAS = {
    "국토계획법": "국토의 계획 및 이용에 관한 법률",
    "산집법": "산업집적활성화 및 공장설립에 관한 법률",
    "산업집적법": "산업집적활성화 및 공장설립에 관한 법률",
    "가축분뇨법": "가축분뇨의 관리 및 이용에 관한 법률",
    "개발제한구역법": "개발제한구역의 지정 및 관리에 관한 특별조치법",
}
PLAN_RE = re.compile(r"^(?P<org>.+?)\s*(?:도시|군)\s*계획\s*조례(?:\s*\[[^\]]*\])?$")
ZONE_RE = re.compile(r"^\d+\.\s*(?P<zone>.+?)\s*안에서\s*건축할\s*수\s*(?P<mode>있는|없는)\s*건축물"
                     r"\s*:\s*별표\s*(?P<annex>\d+)")
DEF_RE = re.compile(r"(?:「(?P<law>[^」]+)」|(?P<same>같은\s*법\s*(?P<sfx>시행령|시행규칙)))"
                    r"[^「」(]{0,10}\(\s*이하\s*[“\"‘']?(?P<abbr>법|영|시행령|규칙|시행규칙)[”\"’']?\s*(?:이)?라\s*한다")
JO_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


def _src(source: str) -> sqlite3.Connection:
    """수집 중인 목록 DB도 읽을 수 있게 읽기 전용 + 잠금 대기."""
    return sqlite3.connect(f"file:{DATA / source / 'index.sqlite'}?mode=ro", uri=True, timeout=60)


def name_key(s: str) -> str:
    return re.sub(r"[\s·ㆍ\(\)「」]", "", s or "")


def nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def jo_path(s: str | None) -> str | None:
    m = JO_RE.search(s or "")
    if not m:
        return None
    return f"제{int(m.group(1))}조" + (f"의{int(m.group(2))}" if m.group(2) else "")


def _base_law(name: str) -> str:
    return re.sub(r"\s*시행(령|규칙)$", "", name)


def _load_sources(docs):
    """(source, mst) 목록과 각 메타 행. docs=None 이면 수집 완료 전체."""
    out = []
    for source in ("law", "ordin"):
        db = _src(source)
        db.row_factory = sqlite3.Row
        want = None if docs is None else {m for s, m in docs if s == source}
        cols = {c[1] for c in db.execute(f"PRAGMA table_info({source})")}
        live = " AND COALESCE(superseded, 0)=0" if "superseded" in cols else ""   # 개정·폐지된 판 제외
        for r in db.execute(f"SELECT * FROM {source} WHERE fetched=1{live}"):
            if want is None or r["mst"] in want:
                d = dict(r)
                d.setdefault("item_id", d.get("ordin_id"))   # 구버전 자치법규 목록 스키마
                out.append((source, d))
    return out


def _annex_rows(source, msts):
    db = _src(source)
    rows = defaultdict(list)
    ms = list(msts)
    for i in range(0, len(ms), 900):
        chunk = ms[i:i + 900]
        q = f"SELECT mst, no, branch, title, text FROM annex WHERE mst IN ({','.join('?' * len(chunk))})"
        for mst, no, br, title, text in db.execute(q, chunk):
            rows[mst].append((no, br, title, text))
    return rows


def _abbr_map(doc_name: str, kind: str, units) -> dict:
    """문서 안의 '법'·'영'·'규칙'이 가리키는 법령명."""
    m = {}
    base = _base_law(doc_name)
    if kind not in ("조례", "규칙") and doc_name != base:   # 시행령·시행규칙
        m = {"법": base, "영": base + " 시행령", "규칙": base + " 시행규칙"}
    head = "\n".join(u.text for u in units[:3])
    last = None
    for d in DEF_RE.finditer(head):
        law = d.group("law")
        if law:
            last = law.strip()
        elif d.group("same") and last:
            law = _base_law(last) + " " + d.group("sfx")
        if law:
            abbr = {"시행령": "영", "시행규칙": "규칙"}.get(d.group("abbr"), d.group("abbr"))
            m[abbr] = law
    return m


def build(out_path: Path, docs=None, links_dir: Path | None = None, log=print) -> dict:
    t0 = time.time()
    links_dir = links_dir or DATA / "links"
    out_path = Path(out_path)
    tmp = out_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

    sources = _load_sources(docs)
    annexes = {s: _annex_rows(s, [r["mst"] for src, r in sources if src == s]) for s in ("law", "ordin")}
    report = {"docs": 0, "units": 0, "annex": 0, "annex_text_missing": 0, "annex_items": 0,
              "ordin_jo_without_split": 0, "ordin_jo_numbered": 0,
              "citations": 0, "citations_resolved": 0}
    pending_cites = []          # (doc_id, path, Citation)
    doc_meta = {}               # doc_id -> (name, kind, abbr)
    key_to_doc = {}
    by_lawid, by_ordin_mst = {}, {}

    for i, (source, r) in enumerate(sources, 1):
        path = DATA / source / "xml" / r["mst"][:3] / f"{r['mst']}.xml.gz"
        try:
            root = ET.fromstring(gzip.open(path).read())
        except Exception as e:  # 손상 파일은 건너뛰고 보고
            report.setdefault("parse_errors", []).append(f"{source}:{r['mst']}:{e}")
            continue
        units = law_units(root) if source == "law" else ordin_units(root)
        cur = db.execute("INSERT INTO doc(source, mst, law_id, name, name_key, short_name, kind, org,"
                         " effect_date) VALUES (?,?,?,?,?,?,?,?,?)",
                         (source, r["mst"], r["item_id"], r["name"], name_key(r["name"]),
                          (r.get("field") if source == "law" else None) or None,
                          r["kind"], r["org"], r["effect_date"]))
        did = cur.lastrowid
        report["docs"] += 1
        key_to_doc.setdefault(name_key(r["name"]), did)
        if source == "law":
            by_lawid[r["item_id"]] = did
        else:
            by_ordin_mst[r["mst"]] = did
        doc_meta[did] = (r["name"], r["kind"], _abbr_map(r["name"], r["kind"], units))
        has_child = {u.parent for u in units if u.parent}
        db.executemany("INSERT OR IGNORE INTO unit(doc_id, path, label, title, text, parent_path)"
                       " VALUES (?,?,?,?,?,?)",
                       [(did, u.path, u.label, u.title, u.text, u.parent) for u in units])
        report["units"] += len(units)
        for u in units:
            if "/" not in u.path:
                db.execute("INSERT INTO fts_unit(rowid, body) SELECT unit_id, ? FROM unit"
                           " WHERE doc_id=? AND path=?", (nospace(u.title + u.text), did, u.path))
                if source == "ordin":
                    if re.search(r"\n\s*(\d+\.|[①-⑳])", u.text):
                        report["ordin_jo_numbered"] += 1
                        if u.path not in has_child:
                            report["ordin_jo_without_split"] += 1
            # 항·호·목 단위의 text 는 자기 문장뿐이다. 조 단위 text 는 자식 문장을 모두 이어 붙인 것이라
            # 자식이 있으면 머리 문장(첫 줄)만 본다 — 자식 문장은 자식 단위에서 본다.
            own = u.text if ("/" in u.path or u.path not in has_child) else u.text.split("\n", 1)[0]
            for c in find_citations(own, keep_internal=True):
                pending_cites.append((did, u.path, c))
        for ano, title, text in normalize_annexes(annexes[source].get(r["mst"], []), r["name"]):   # 합본 쪼개기·머리말 번호
            cur = db.execute("INSERT OR IGNORE INTO annex(doc_id, no, title, text) VALUES (?,?,?,?)",
                             (did, ano, title, text))
            report["annex"] += 1
            if not text:
                report["annex_text_missing"] += 1
                continue
            aid = cur.lastrowid
            items = split_annex(text)
            report["annex_items"] += len(items)
            for p, lab, t in items:
                c2 = db.execute("INSERT INTO annex_item(annex_id, path, label, text) VALUES (?,?,?,?)",
                                (aid, p, lab, t))
                db.execute("INSERT INTO fts_annex(rowid, body) VALUES (?,?)",
                           (c2.lastrowid, nospace(t)))
                leaf = not any(q.startswith(p + "/") for q, _, _ in items)
                if leaf:
                    for c in find_citations(t, keep_internal=True):
                        pending_cites.append((did, f"별표{ano}:{p}", c))
        if i % 5000 == 0:
            db.commit()
            log(f"[build] 문서 {i}/{len(sources)} ({time.time() - t0:.0f}s)")

    # 별칭
    for did, (name, _, _) in doc_meta.items():
        db.execute("INSERT OR IGNORE INTO alias VALUES (?,?)", (name_key(name), did))
    for (sn, did) in db.execute("SELECT short_name, doc_id FROM doc WHERE short_name IS NOT NULL").fetchall():
        db.execute("INSERT OR IGNORE INTO alias VALUES (?,?)", (name_key(sn), did))
    for short, full in MANUAL_ALIAS.items():
        if name_key(full) in key_to_doc:
            db.execute("INSERT OR IGNORE INTO alias VALUES (?,?)", (name_key(short), key_to_doc[name_key(full)]))

    # 조례 연계(lnkLsOrdJo): 조례 조 → 법령 조 (상위 방향)
    ordjo_laws = defaultdict(set)   # ordin doc -> 연계 법령 doc
    ordjo = links_dir / "ordjo.jsonl"
    n_ordjo = 0
    if ordjo.exists():
        with ordjo.open() as f:
            for line in f:
                row = json.loads(line)
                od, ld = by_ordin_mst.get(row.get("ordin_mst")), by_lawid.get(row.get("law_id"))
                if not (od and ld):
                    continue
                ordjo_laws[od].add(ld)
                db.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?,?)",
                           (od, jo_path(row["ordin_jo"]), ld, jo_path(row["law_jo"]), "조례연계",
                            "lnkLsOrdJo"))
                n_ordjo += 1
    report["ordjo_links"] = n_ordjo

    # 인용
    kind_of = {did: k for did, (_, k, _) in doc_meta.items()}
    for did, fpath, c in pending_cites:
        report["citations"] += 1
        target = None
        if c.rel == "self":
            target = did
        elif c.law:
            target = key_to_doc.get(name_key(c.law))
        elif c.rel in ("법", "영", "규칙"):
            nm = doc_meta[did][2].get(c.rel)
            if nm:
                target = key_to_doc.get(name_key(nm))
            elif did in ordjo_laws:  # 약칭 정의가 없는 조례: 연계 법령 중 종류가 맞는 것
                want = {"법": ("법률",), "영": ("대통령령",), "규칙": ("총리령",)}[c.rel]
                cands = [d for d in ordjo_laws[did] if kind_of.get(d) in want
                         or (c.rel == "규칙" and kind_of.get(d, "").endswith("부령"))]
                target = cands[0] if len(cands) == 1 else None
        if target is None:
            continue
        report["citations_resolved"] += 1
        db.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?,?)",
                   (did, fpath, target, citation_path(c), "인용", c.span[:80]))

    # 체계: "X 시행령" → "X", "X 시행규칙" → "X 시행령"(없으면 "X")
    for did, (name, _, _) in doc_meta.items():
        base = _base_law(name)
        if base == name:
            continue
        parent = None
        if name.endswith("시행규칙"):
            parent = key_to_doc.get(name_key(base + " 시행령"))
        parent = parent or key_to_doc.get(name_key(base))
        if parent:
            db.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?,?)",
                       (did, None, parent, None, "체계", "법령명"))

    # 용도지역 → 시행령 별표
    enf = key_to_doc.get(name_key("국토의 계획 및 이용에 관한 법률 시행령"))
    if enf:
        for path, text in db.execute("SELECT path, text FROM unit WHERE doc_id=? AND path LIKE '제71조/1/%'"
                                     " AND path NOT LIKE '제71조/1/%/%'", (enf,)):
            m = ZONE_RE.match(text)
            if m:
                db.execute("INSERT OR REPLACE INTO zone_map VALUES (?,?,?,?)",
                           (nospace(m.group("zone")), path, m.group("annex"),
                            "can" if m.group("mode") == "있는" else "cannot"))

    # 시군구 → 도시·군계획 조례
    report["region_unmatched"] = _region_plan(db)
    report["plan_annex_missing"] = [n for (n,) in db.execute(
        "SELECT d.name FROM region_plan r JOIN doc d ON d.doc_id=r.doc_id GROUP BY d.doc_id "
        "HAVING (SELECT count(*) FROM annex a WHERE a.doc_id=d.doc_id AND a.title LIKE '%녹지지역%'"
        " AND a.text IS NOT NULL AND length(a.text) > 100) = 0"
        " AND (SELECT count(*) FROM unit u WHERE u.doc_id=d.doc_id AND u.path NOT LIKE '%/%'"
        " AND u.title LIKE '%녹지지역%' AND u.title LIKE '%건축%') = 0")]   # 본문 조문에 두는 곳 제외
    report["citation_resolved_rate"] = round(report["citations_resolved"] / max(report["citations"], 1), 3)
    report["seconds"] = round(time.time() - t0)

    db.executescript(POST_INDEX)
    db.execute("INSERT INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    db.execute("INSERT INTO meta VALUES ('report', ?)", (json.dumps(report, ensure_ascii=False),))
    db.commit()
    db.close()
    os.replace(tmp, out_path)
    return report


def _region_plan(db) -> list[str]:
    """도시·군계획 조례를 시군구 이름 여러 표기로 색인한다. 계획 조례가 없는 지자체 목록을 돌려준다."""
    # 자치구·행정시가 광역 조례를 쓰는 곳. 강원·전북 '특별자치도'의 시군은 자기 조례가 있어 제외(제주만 도 조례)
    metro = ("특별시", "광역시", "특별자치시")
    # 지자체 목록은 부분 빌드여도 전체 목록에서 가져온다(동명 자치구 판별에 필요)
    src = _src("ordin")
    orgs = [o for (o,) in src.execute("SELECT DISTINCT org FROM ordin WHERE org IS NOT NULL")]
    plans = {}
    for did, name, org in db.execute("SELECT doc_id, name, org FROM doc WHERE source='ordin'"):
        if PLAN_RE.match(name):
            plans[org] = did

    def keys(org):
        toks = org.split()
        out = {org, toks[-1]}
        last = toks[-1]
        if len(last) > 2 and last[-1] in "시군구":
            out.add(last[:-1])
        if len(toks) > 1:
            short = re.sub(r"(특별시|광역시|특별자치시|특별자치도|도)$", "", toks[0])
            out.add(f"{short} {last}")
        return out

    unmatched = []
    for org in orgs:
        toks = org.split()
        sido = toks[0]
        if len(toks) == 1 and not (sido.endswith(metro) or sido.startswith("제주")):
            continue  # 도 단위 조례는 시군구 해석 대상이 아니다
        did = plans.get(org)
        if len(toks) > 1 and (sido.endswith(metro) or sido.startswith("제주")) and plans.get(sido):
            # 광역시 자치구·제주 행정시: 용도지역 건축제한·건폐율은 광역 조례에 있다
            # (자치구 도시계획 조례는 위원회 등 위임 사항만 둔다)
            did = plans[sido]
        if did is None:
            unmatched.append(org)
            continue
        for k in keys(org):
            db.execute("INSERT OR IGNORE INTO region_plan VALUES (?,?,?)", (k, sido, did))
    for extra in ("제주시", "서귀포시"):
        did = plans.get("제주특별자치도")
        if did:
            db.execute("INSERT OR IGNORE INTO region_plan VALUES (?,?,?)", (extra, "제주특별자치도", did))
    return sorted(unmatched)
