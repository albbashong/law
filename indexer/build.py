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
from indexer.hierarchy import law_nodes, relatives
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
CREATE INDEX ix_unit_title ON unit(doc_id, path, title) WHERE title IS NOT NULL AND title!='';
CREATE INDEX ix_annex_title ON annex(doc_id, title) WHERE title IS NOT NULL AND title!='';
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
# 시행령에서 용도지역의 건축제한·건폐율·용적률을 정한 조문 제목의 머리말(법령 문구) — 이 조문들에 법제처 연계로
# 이어진 조례가 그 지자체의 도시·군계획 조례다
# 하위 법령의 제1조(목적)가 상위법을 가리키는 문구 — "이 영은 「○○법」에서 위임된 사항과 …" (법령 문구)
PURPOSE_ARTICLE = "제1조"
PURPOSE_RE = re.compile(r"「([^」]+)」[^「」]{0,40}?위임")
PLAN_DECREE = "국토의 계획 및 이용에 관한 법률 시행령"   # 용도지역별 건축제한을 정한 법령
PLAN_TITLE_HEAD = "용도지역"
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


def _load_sources(docs):
    """((source, 메타 행) 목록, 빠진 옛 판 수). docs=None 이면 수집 완료 전체.

    같은 지자체의 같은 이름 자치법규가 두 판 살아 있으면(목록에 옛 판이 남은 것) 시행일이 늦은 판만 남긴다.
    """
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
    newest = {}
    for source, d in out:
        if source == "ordin":
            key = (d["name"], d["org"])
            if key not in newest or (d["effect_date"] or "", d["mst"]) > (newest[key]["effect_date"] or "", newest[key]["mst"]):
                newest[key] = d
    kept = [(s, d) for s, d in out if s != "ordin" or newest[(d["name"], d["org"])] is d]
    return kept, len(out) - len(kept)


def pick_named(cands: list, from_doc: int, org_of: dict):
    """이름이 가리키는 문서 — 하나면 그것, 여럿(다른 지자체의 같은 이름 조례)이면 인용한 문서와 같은 지자체의 것.
    가릴 수 없으면 잇지 않는다(먼저 들어온 것에 잘못 잇지 않게)."""
    if len(cands) == 1:
        return cands[0]
    same = [c for c in cands if org_of.get(c) and org_of.get(c) == org_of.get(from_doc)]
    return same[0] if len(same) == 1 else None


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


SAME_LAW_TIER = {"시행령": "영", "시행규칙": "규칙"}


def _abbr_map(units) -> dict:
    """문서가 스스로 정한 약칭 — '법'·'영'·'규칙' → 법령명, 또는 ("같은 법", 법령명, '영'|'규칙').

    "같은 법 시행령"은 이름을 지어내지 않고 그 법의 체계도에서 찾도록 표시만 남긴다.
    """
    m = {}
    head = "\n".join(u.text for u in units[:3])
    last = None
    for d in DEF_RE.finditer(head):
        law = d.group("law")
        abbr = SAME_LAW_TIER.get(d.group("abbr"), d.group("abbr"))
        if law:
            last = law.strip()
            m[abbr] = last
        elif d.group("same") and last:
            m[abbr] = ("같은 법", last, SAME_LAW_TIER[d.group("sfx")])
    return m


def _hierarchy(links_dir: Path) -> dict:
    """법령체계도 자료 → 법령ID 별 Node. 같은 법령이 여러 체계도에 나오면 상위가 적힌 것을 쓴다."""
    nodes = {}
    path = links_dir / "stmd.jsonl"
    if path.exists():
        with path.open() as f:
            for line in f:
                if line.strip():
                    for n in law_nodes(json.loads(line)["xml"]):
                        if n.law_id not in nodes or (n.parent and not nodes[n.law_id].parent):
                            nodes[n.law_id] = n
    return nodes


def build(out_path: Path, docs=None, links_dir: Path | None = None, log=print) -> dict:
    t0 = time.time()
    links_dir = links_dir or DATA / "links"
    out_path = Path(out_path)
    tmp = out_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)

    sources, older = _load_sources(docs)
    annexes = {s: _annex_rows(s, [r["mst"] for src, r in sources if src == s]) for s in ("law", "ordin")}
    report = {"docs": 0, "units": 0, "annex": 0, "annex_text_missing": 0, "annex_items": 0,
              "ordin_jo_without_split": 0, "ordin_jo_numbered": 0, "unit_path_dups": 0,
              "citations": 0, "citations_resolved": 0, "same_name_older_dropped": older}
    pending_cites = []          # (doc_id, path, Citation)
    doc_meta = {}               # doc_id -> (name, kind, abbr)
    key_to_doc = {}
    kind_of_source = {}                          # 문서 → 'law'|'ordin'
    key_docs, org_of = defaultdict(list), {}     # 이름 → 문서들(다른 지자체의 같은 이름 조례), 문서 → 지자체
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
        key_docs[name_key(r["name"])].append(did)
        org_of[did] = r["org"]
        kind_of_source[did] = source
        if source == "law":
            by_lawid[r["item_id"]] = did
        else:
            by_ordin_mst[r["mst"]] = did
        doc_meta[did] = (r["name"], r["kind"], _abbr_map(units))
        has_child = {u.parent for u in units if u.parent}
        cur = db.executemany("INSERT OR IGNORE INTO unit(doc_id, path, label, title, text, parent_path)"
                             " VALUES (?,?,?,?,?,?)",
                             [(did, u.path, u.label, u.title, u.text, u.parent) for u in units])
        report["units"] += cur.rowcount                      # 실제로 들어간 수
        report["unit_path_dups"] += len(units) - cur.rowcount  # 같은 경로가 또 나와 빠진 단위
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
            # 번호는 normalize_annexes 가 문서 안에서 하나씩으로 만든다 — 겹치면 조용히 넘기지 않고 빌드가 멈춘다
            cur = db.execute("INSERT INTO annex(doc_id, no, title, text) VALUES (?,?,?,?)",
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

    # 체계: 법제처 법령체계도의 상하위 — 법령 이름 꼴로 짐작하지 않는다
    nodes = _hierarchy(links_dir)
    rel_doc, lower_docs = {}, set()     # doc -> {'법'|'영'|'규칙': doc}, 상위 법령이 있는 문서
    report["hierarchy_links"] = 0
    for law_id, rel in relatives(nodes).items():
        did = by_lawid.get(law_id)
        if not did:
            continue
        rel_doc[did] = {k: by_lawid[v] for k, v in rel.items() if v in by_lawid}
        parent = by_lawid.get(nodes[law_id].parent)
        if parent:
            lower_docs.add(did)
            db.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?,?)",
                       (did, None, parent, None, "체계", "법령체계도"))
            report["hierarchy_links"] += 1
    # 체계도에 없는 법령(법제처 자료의 빈 곳) — 제1조(목적)의 "「○○법」에서 위임된 사항"이 가리키는 법령을 상위로 본다.
    # 체계도가 있는 하위 법령 2,572개로 견줘 99.7% 가 체계도와 같았다(2026-11-20). 이름 꼴('… 시행령')은 보지 않는다
    report["hierarchy_by_purpose"] = 0
    for law_id, did in by_lawid.items():
        if law_id in nodes:
            continue
        row = db.execute("SELECT text FROM unit WHERE doc_id=? AND path=?", (did, PURPOSE_ARTICLE)).fetchone()
        named = [key_to_doc.get(name_key(n)) for n in PURPOSE_RE.findall((row[0] if row else "") or "")]
        parent = next((d for d in named if d and d != did and kind_of_source.get(d) == "law"), None)
        if parent:
            rel_doc[did] = {"법": rel_doc.get(parent, {}).get("법", parent), **{k: v for k, v in rel_doc.get(parent, {}).items() if k != "법"}}
            lower_docs.add(did)
            db.execute("INSERT OR IGNORE INTO link VALUES (?,?,?,?,?,?)", (did, None, parent, None, "체계", "목적 조문"))
            report["hierarchy_by_purpose"] += 1
    report["hierarchy_missing"] = sum(1 for law_id, did in by_lawid.items() if law_id not in nodes and did not in lower_docs)

    # 인용
    kind_of = {did: k for did, (_, k, _) in doc_meta.items()}
    for did, fpath, c in pending_cites:
        report["citations"] += 1
        target = None
        if c.rel == "self":
            target = did
        elif c.law:
            target = pick_named(key_docs.get(name_key(c.law), []), did, org_of)
        elif c.rel in ("법", "영", "규칙"):
            nm = doc_meta[did][2].get(c.rel)
            if isinstance(nm, tuple):       # "같은 법 시행령" — 그 법의 체계도에서 찾는다
                target = rel_doc.get(key_to_doc.get(name_key(nm[1])), {}).get(nm[2])
            elif nm:
                target = key_to_doc.get(name_key(nm))
            elif did in lower_docs and c.rel in rel_doc[did]:   # 하위 법령 본문의 '법'·'영'·'규칙'
                target = rel_doc[did][c.rel]
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

    # 용도지역 → 시행령 별표. 조문 번호가 아니라 문구("… 안에서 건축할 수 있는 건축물: 별표 N")로 찾는다
    enf = key_to_doc.get(name_key(PLAN_DECREE))
    if enf:
        for path, text in db.execute("SELECT path, text FROM unit WHERE doc_id=? ORDER BY unit_id", (enf,)).fetchall():
            m = ZONE_RE.match(text or "")
            if m:
                db.execute("INSERT OR REPLACE INTO zone_map VALUES (?,?,?,?)",
                           (nospace(m.group("zone")), path, m.group("annex"),
                            "can" if m.group("mode") == "있는" else "cannot"))
        if not db.execute("SELECT 1 FROM zone_map LIMIT 1").fetchone():
            raise RuntimeError(f"용도지역 표가 비었습니다 — 「{PLAN_DECREE}」에서 용도지역별 건축제한 문구를 찾지 못했습니다."
                               " 문구가 바뀌었는지 확인하세요(indexer/build.py ZONE_RE)")

    # 시군구 → 도시·군계획 조례
    report["region_unmatched"] = _region_plan(db, enf)
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


def _region_plan(db, enf) -> list[str]:
    """지자체 → 도시·군계획 조례. 계획 조례가 정해지지 않은 지자체 목록을 돌려준다.

    계획 조례는 이름 꼴이 아니라 법제처 법령–조례 연계로 찾는다: 시행령(enf)의 용도지역 조문(PLAN_TITLE_HEAD)에 가장
    많이 이어진 조례. 자기 계획 조례가 없는 지자체(자치구 등)는 ① 자기 조례가 인용한 계획 조례 ② 같은 시도의 그런
    지자체들이 인용한 계획 조례가 하나로 모이면 그것 ③ 시도의 계획 조례 순으로 잇는다. 소속 지자체 어디에도 자기 계획
    조례가 없는 시도(광역시 등)는 시도 이름으로도 찾을 수 있게 넣는다 — 지자체 조례가 없는 행정시가 여기에 기댄다.
    """
    # 지자체 목록은 부분 빌드여도 전체 목록에서 가져온다(동명 자치구 판별에 필요)
    orgs = [o for (o,) in _src("ordin").execute("SELECT DISTINCT org FROM ordin WHERE org IS NOT NULL")]
    linked = defaultdict(lambda: defaultdict(int))      # org -> 계획 조문에 이어진 조례 doc -> 연결 수
    if enf:
        heads = [p for p, t in db.execute("SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%'", (enf,))
                 if nospace(t or "").startswith(PLAN_TITLE_HEAD)]
        for did, org, path in db.execute("SELECT l.from_doc, d.org, l.to_path FROM link l JOIN doc d ON d.doc_id=l.from_doc"
                                         " WHERE l.kind='조례연계' AND l.to_doc=?", (enf,)).fetchall():
            if org and (path or "").split("/")[0] in heads:
                linked[org][did] += 1
    plans = {org: max(c, key=lambda d: (c[d], -d)) for org, c in linked.items()}
    plan_docs = set(plans.values())
    cited = defaultdict(lambda: defaultdict(int))       # org -> 자기 조례가 인용한 계획 조례 -> 횟수
    for org, to in db.execute("SELECT d.org, l.to_doc FROM link l JOIN doc d ON d.doc_id=l.from_doc"
                              " WHERE l.kind='인용' AND d.source='ordin'").fetchall():
        if org and to in plan_docs and org not in plans:
            cited[org][to] += 1
    by_cite = {org: max(c, key=lambda d: (c[d], -d)) for org, c in cited.items()}
    shared = defaultdict(set)                           # 시도 -> 계획 조례 없는 소속 지자체들이 인용한 계획 조례
    for org, did in by_cite.items():
        shared[org.split()[0]].add(did)

    def keys(org):
        last = org.split()[-1]
        return {org, last} | ({last[:-1]} if len(last) > 2 and last[-1] in "시군구" else set())

    unmatched, own = [], defaultdict(bool)              # own: 시도 -> 자기 계획 조례를 가진 소속 지자체가 있는가
    for org in orgs:
        toks = org.split()
        if len(toks) == 1:
            continue
        sido = toks[0]
        own[sido] |= org in plans
        did = plans.get(org) or by_cite.get(org) or (
            next(iter(shared[sido])) if len(shared[sido]) == 1 else plans.get(sido))
        if did is None:
            unmatched.append(org)
            continue
        for k in keys(org):
            db.execute("INSERT OR IGNORE INTO region_plan VALUES (?,?,?)", (k, sido, did))
    for org in orgs:
        if len(org.split()) == 1 and org in plans and not own[org]:
            for k in keys(org):
                db.execute("INSERT OR IGNORE INTO region_plan VALUES (?,?,?)", (k, org, plans[org]))
    return sorted(unmatched)
