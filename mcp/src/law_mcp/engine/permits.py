"""관련 인허가 조문을 법령 데이터에서 찾는다 — 지목·용도별 표를 두지 않는다.

법률 제2조(정의)에서 그 말을 정의하는 법률을 찾고, 그 법률에서 제목에 그 말과 '허가'·'승인'이 든 조문을 고른다.
  용도: "공장"을 정의하는 법률 → 제목에 '공장'+'승인'      → 산업집적법 제13조(공장설립등의 승인)
  지목: 정의 본문에 '지목'과 그 지목이 나오는 용어("농지") → 제목에 '농지'+'전용'+'허가' → 농지법 제34조
정의의 단서("다만, … 제외한다")는 그 용어에 들지 않는 것이므로 매칭에서 뺀다.
"""
import re

from law_mcp.engine.follow import Ref, family, node
from law_mcp.store.db import nospace

PLAN_LAW = family("국토의 계획 및 이용에 관한 법률 시행령")
DEF = re.compile(r"[“\"]([^”\"]{1,30})[”\"]\s*(?:이)?(?:란|이란|라\s*함은|이라\s*함은|라고\s*함은|이라고\s*함은)")
GRANT = re.compile(r"허가|승인")
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
        if best:     # 그 말을 정의하는 법률이 여럿이면 그 말을 제목에 가장 많이 쓰는 법률(특례법 잡음 제외)
            found.append(best)
            seen_docs.add(best[1])
    return [r for r in (node(store, d, p) for _, d, p in found[:2]) if r]


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
