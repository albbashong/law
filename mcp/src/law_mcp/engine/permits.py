"""관련 인허가 조문을 법령 데이터에서 찾는다 — 지목·용도별 표를 두지 않는다.

법률 제2조(정의)에서 그 말을 정의하는 법률을 찾고, 그 법률에서 제목에 그 말과 '허가'·'승인'이 든 조문을 고른다.
  용도: "공장"을 정의하는 법률 → 제목에 '공장'+'승인'      → 산업집적법 제13조(공장설립등의 승인)
  지목: 정의 본문에 '지목'과 그 지목이 나오는 용어("농지") → 제목에 '농지'+'전용'+'허가' → 농지법 제34조
정의의 단서("다만, … 제외한다")는 그 용어에 들지 않는 것이므로 매칭에서 뺀다.
"""
import re

from law_mcp.engine.follow import DEEM, Ref, family, node, rank
from law_mcp.store.db import nospace

PLAN_LAW = family("국토의 계획 및 이용에 관한 법률 시행령")
BUILDING_LAW = family("건축법 시행령")
# 대지가 도로에 접해야 하는 기준과, 그 기준을 적용하지 않는 지역을 정한 조문의 제목(법령 용어)
ROAD_TITLE, EXEMPT_TITLE = "대지와 도로의 관계", "적용 제외"
DEF = re.compile(r"[“\"]([^”\"]{1,30})[”\"]\s*(?:이)?(?:란|이란|라\s*함은|이라\s*함은|라고\s*함은|이라고\s*함은)")
GRANT = re.compile(r"허가|승인")
DELEGATE = re.compile(r"대통령령으로\s*정")   # 위임 문구(법률 용어)
# 제2조 밖의 정의: "… 행위(이하 "개발행위"라 한다)"
ABBR = re.compile(r"이하\s*[“\"]([^”\"]{1,20})[”\"]\s*(?:이)?라\s*한다")
_defs: dict = {}


def _clause(text: str) -> str:
    return re.split(r"다만\s*[,，]", text or "", maxsplit=1)[0]


def definitions(store) -> list[dict]:
    """법률 제2조의 용어 정의 [{doc_id, term, text}] — text 는 그 호와 하위 목(단서 제외)."""
    hit = _defs.get(id(store))
    if hit is not None:
        return hit
    ids = [r["doc_id"] for r in store._all("SELECT doc_id FROM doc WHERE source='law' AND kind='법률'")]
    out = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = store._all(f"SELECT doc_id, path, text FROM unit WHERE doc_id IN ({','.join('?' * len(chunk))})"
                          " AND (path='제2조' OR path GLOB '제2조/*') ORDER BY unit_id", chunk)
        by_doc: dict = {}
        for r in rows:
            by_doc.setdefault(r["doc_id"], []).append(r)
        for doc_id, units in by_doc.items():
            has_kids = len(units) > 1
            for u in units:
                if u["path"] == "제2조" and has_kids:
                    continue        # 조 전체 텍스트는 하위 호를 이어 붙인 것 — 호 단위로 본다
                own = _clause(u["text"])
                m = DEF.search(own)
                if not m:
                    continue
                kids = [_clause(k["text"]) for k in units if k["path"].startswith(u["path"] + "/")]
                term = nospace(re.sub(r"\(.*?\)", "", m.group(1)))
                out.append({"doc_id": doc_id, "path": u["path"], "term": term, "text": "\n".join([own] + kids),
                            "kind": "정의"})
        rows = store._all(f"SELECT doc_id, path, text FROM unit WHERE doc_id IN ({','.join('?' * len(chunk))})"
                          " AND instr(text, '라 한다') > 0 ORDER BY unit_id", chunk)
        seen = set()
        for r in sorted(rows, key=lambda r: -r["path"].count("/")):     # 같은 정의면 가장 깊은 단위(항·호)만
            for m in ABBR.finditer(r["text"] or ""):
                term = nospace(m.group(1))
                key = (r["doc_id"], term, r["path"].split("/")[0])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"doc_id": r["doc_id"], "path": r["path"], "term": term, "text": r["text"], "kind": "약칭"})
    _defs[id(store)] = out
    return out


def permits_for_term(store, terms, must: tuple = ()) -> list[Ref]:
    """말마다 그 말을 정의하는 법률 하나(제목에 그 말을 가장 많이 쓰는 것)에서 제목에 그 말·'허가|승인'(·must)이 든 첫 조문."""
    found = []
    seen_docs = set()
    for t in dict.fromkeys(nospace(x) for x in terms if x):
        if len(t) < 2:
            continue
        docs = {d["doc_id"] for d in definitions(store) if d["term"] == t and d["kind"] == "정의"} - seen_docs
        best = None
        for doc_id in docs:
            arts = store._all("SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id",
                              (doc_id,))
            titled = [a for a in arts if t in nospace(a["title"])]
            grant = [a for a in titled if GRANT.search(a["title"] or "") and all(x in a["title"] for x in must)]
            if grant and (best is None or len(titled) > best[0]):
                best = (len(titled), doc_id, grant[0]["path"])
        if not best and not must:
            best = _grant_by_scale(store, t, docs)
        if best:     # 그 말을 정의하는 법률이 여럿이면 그 말을 제목에 가장 많이 쓰는 법률(특례법 잡음 제외)
            found.append(best)
            seen_docs.add(best[1])
    pairs = [(b[1], b[2]) for b in found[:2]] + [b[3] for b in found[:2] if len(b) > 3]
    return [r for r in (node(store, d, p) for d, p in pairs) if r]


def _grant_by_scale(store, term: str, docs) -> tuple | None:
    """승인 조문 제목에 그 말이 없을 때 — 그 말을 정의한 법률의 하위 법령에서, 승인 대상 규모를 그 말로 나눠 정한
    승인·허가 조문("2. 공동주택: 30세대")을 찾아 그 조문이 위임받은 법률 조문을 고른다(주택법 시행령 제27조 → 법 제15조)."""
    item = re.compile(rf"(^|\n)\s*\d+\.\s*{re.escape(term)}\s*[:：]")
    laws = store._all("SELECT doc_id, name, kind FROM doc WHERE source='law'")
    for doc_id in sorted(docs):
        top = store.doc(doc_id)
        lower = [d for d in laws if family(d["name"]) == family(top["name"]) and rank(d["kind"]) > rank(top["kind"])]
        for d in sorted(lower, key=lambda d: rank(d["kind"])):
            for a in store._all("SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id",
                                (d["doc_id"],)):
                if not GRANT.search(a["title"] or ""):
                    continue
                hit = next((u for u in store.units_with_prefix(d["doc_id"], a["path"])
                            if u["path"] != a["path"] and item.search(u["text"] or "")), None)
                if not hit:
                    continue
                up = [l["to_path"] for l in store.links_from_prefix(d["doc_id"], a["path"]) if l["to_doc"] == doc_id]
                if up:   # 법률 조문과 함께 규모 기준을 정한 하위 조문도 — 대상이 되는 규모가 답에 보이게
                    return (0, doc_id, up[0].split("/", 1)[0], (d["doc_id"], hit["path"]))
                return (0, d["doc_id"], a["path"])
    return None


def deeming_of(store, permit: Ref) -> Ref | None:
    """그 허가·승인을 받으면 다른 인허가를 받은 것으로 본다고 정한 같은 법의 항(주택법 제15조 → 제19조①)."""
    for l in store.links_to_prefix(permit.doc_id, permit.path, from_doc=permit.doc_id):
        u = store.unit(permit.doc_id, l["from_path"] or "")
        if u and "/" in l["from_path"] and DEEM.search((u["text"] or "").split("\n", 1)[0]):
            return node(store, permit.doc_id, l["from_path"])
    return None


def land_permits(store, land_category: str | None) -> list[Ref]:
    """지목 → 그 지목을 정의에 담은 용어(농지·산지 등)의 전용허가 조문."""
    if not land_category:
        return []
    # 조사가 붙는 것은 허용("임야인 토지", "전ㆍ답")하되 더 긴 낱말의 일부("전용")는 아니다
    tok = re.compile(rf"(?<![가-힣]){re.escape(land_category)}(?:(?![가-힣])|(?=(?:인|이|은|는|을|를|과|와|및|의|으로|로)(?![가-힣])))")
    terms = [d["term"] for d in definitions(store) if d["kind"] == "정의" and "지목" in d["text"] and tok.search(d["text"])]
    return permits_for_term(store, terms, must=("전용",))


def development_permit(store) -> Ref | None:
    """국토계획법의 '개발행위의 허가' 조문 (제목으로 찾는다)."""
    d = store.doc_named(PLAN_LAW)
    if not d:
        return None
    for a in store._all("SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id",
                        (d["doc_id"],)):
        if nospace(a["title"]) == "개발행위의허가":
            return node(store, d["doc_id"], a["path"])
    return None


def titled(store, law: str, title: str) -> Ref | None:
    """법령에서 제목이 그 법령 용어인 조문."""
    d = store.doc_named(law)
    if not d:
        return None
    for a in store._all("SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id",
                        (d["doc_id"],)):
        if nospace(a["title"]) == nospace(title):
            return node(store, d["doc_id"], a["path"])
    return None


def road_access(store) -> list[Ref]:
    """대지와 도로의 관계(법률·위임받은 시행령)와 그 적용 제외 조문 — 필지의 읍·면·동·용도지역으로 적용 여부를 본다."""
    road = titled(store, BUILDING_LAW, ROAD_TITLE)
    exempt = titled(store, BUILDING_LAW, EXEMPT_TITLE)
    if road and exempt:   # 적용 제외 조문 중 대지와 도로 조문 번호를 인용한 항 — 그 조건이 발췌에 보이게
        key = nospace(road.article)
        for u in store._all("SELECT path, text FROM unit WHERE doc_id=? AND path GLOB ? ORDER BY unit_id",
                            (exempt.doc_id, exempt.path + "/*")):
            if re.search(re.escape(key) + r"(?!\d|의\d)", nospace(u["text"] or "")):
                exempt = node(store, exempt.doc_id, u["path"]) or exempt
                break
    decree = titled(store, f"{BUILDING_LAW} 시행령", ROAD_TITLE)
    if road and decree:   # 법률이 대통령령에 맡긴 항(도로 너비·접하는 길이)을 인용하는 시행령의 항 — 조 전체는 앞 항만 담긴다
        given = [u["path"] for u in store.units_with_prefix(road.doc_id, road.path)
                 if u["path"].count("/") == 1 and DELEGATE.search(u["text"] or "")]
        keys = [nospace(node(store, road.doc_id, p).article) for p in given]
        for u in store._all("SELECT path, text FROM unit WHERE doc_id=? AND path GLOB ? ORDER BY unit_id",
                            (decree.doc_id, decree.path + "/*")):
            if u["path"].count("/") == 1 and any(re.search(re.escape(k) + r"(?!\d|제\d+호)", nospace(u["text"] or ""))
                                                  for k in keys):
                decree = node(store, decree.doc_id, u["path"]) or decree
                break
    found = [road, decree, exempt]
    return [r for r in found if r]
