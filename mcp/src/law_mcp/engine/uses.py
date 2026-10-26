"""건축물 용도 해석 — 건축법 시행령 별표 1(용도별 건축물의 종류) 원문에서 바로 찾는다.

사람이 만든 단어 사전을 두지 않는다. 별표 1의 호 이름("공장")·목에 열거된 시설명("일반음식점")과
일치하는 것만 돌려주고, 원문에 없는 표현("카페")은 빈 목록 — 호출 측이 용도명으로 바꿔 다시 부른다.
"""
import re

from law_mcp.store.db import nospace

USE_LAW = "건축법 시행령"
USE_ANNEX = "1"
FACILITY_CHARS = 10      # 이보다 긴 목 머리는 시설명이 아니라 조건을 단 설명("…으로서 …아니하는 것")
_cache: dict = {}


def _strip_no(line: str) -> str:
    return re.sub(r"^\s*\d+(?:의\d+)?\.\s*", "", line)


def _law_names(store) -> list[str]:
    """건축법 제2조제2항의 용도 구분 이름 — 별표 1 호 머리에서 이름과 설명이 한 줄로 붙은 경우를 가른다."""
    d = store.doc_named(USE_LAW.replace(" 시행령", ""))
    if not d:
        return []
    rows = store._all("SELECT text FROM unit WHERE doc_id=? AND path GLOB '제2조/2/*'", (d["doc_id"],))
    return [re.sub(r"\(.*?\)", "", _strip_no(r["text"])).strip() for r in rows]


def _name(first_line: str, law_names: list[str]) -> str:
    """'1. 단독주택[단독주택의 형태를 …' → '단독주택', '17. 공장 물품의 제조·가공 …' → '공장'"""
    s = _strip_no(first_line)
    pre = [n for n in law_names if n and nospace(s).startswith(nospace(n))]
    if pre:
        return max(pre, key=lambda n: len(nospace(n)))
    return re.split(r"[\[\(「/]", s, maxsplit=1)[0].strip()


def facility_terms(text: str) -> list[str]:
    """목 머리에 열거된 시설명들: '러. 안마시술소, 노래연습장' → [안마시술소, 노래연습장].

    "…등 일용품을 판매하는 소매점으로서"처럼 물품을 늘어놓고 설명으로 시설을 부르면 설명 끝의 시설명(소매점).
    "…으로서/로서" 뒤는 조건, "「법」 …에 따른" 은 근거 표기라 뗀다.
    """
    s = re.sub(r"^\s*[가-힣]\.\s*", "", text.split("\n", 1)[0])
    s = re.sub(r"「[^」]*」", "", s)                    # 근거 법령명 — 안의 '및'·쉼표로 나누지 않게
    s = re.split(r"[:：\(\[]|(?<=[가-힣])으?로서\s", s + " ", maxsplit=1)[0]
    enum, _, desc = s.partition(" 등 ")
    out = []
    for x in re.split(r",|\s및\s", enum):
        x = re.sub(r"^.*에\s*따른\s*", "", x).strip()
        if x and "·" not in x and "ㆍ" not in x and not nospace(x).startswith("그밖에"):   # 물품 목록·"그 밖에 이와 유사한 것"은 빼고
            out.append(nospace(x))
    if not out and desc.strip() and len(w := nospace(desc.split()[-1])) >= 3 and "·" not in w:
        out.append(w)       # 열거가 물품뿐이면 설명이 부르는 시설명
    return [t for t in dict.fromkeys(out) if 2 <= len(t) <= FACILITY_CHARS]


def facility_term(text: str) -> str:
    """목의 첫 시설명 (없으면 빈 문자열)."""
    terms = facility_terms(text)
    return terms[0] if terms else ""


def catalog(store) -> list[dict]:
    """별표 1의 호 목록 [{ho, name, label, items:[{path,label,text}]}] (store 마다 한 번 읽는다)."""
    hit = _cache.get(id(store))
    if hit is not None:
        return hit
    d = store.doc_named(USE_LAW)
    a = store.annex(d["doc_id"], USE_ANNEX) if d else None
    out: list[dict] = []
    law_names = _law_names(store)
    if a:
        for it in store.annex_items(a["annex_id"]):
            top, _, rest = it["path"].partition("/")
            if not rest:
                out.append({"ho": top, "name": _name(it["text"].split("\n", 1)[0], law_names), "label": it["label"],
                            "text": it["text"], "items": []})
            elif out and out[-1]["ho"] == top and rest.count("/") == 0:
                out[-1]["items"].append(it)
    _cache[id(store)] = out
    return out


def match_use(store, use: str) -> list[dict]:
    """사용자 표현 → 별표 1 호 후보 [{ho, name, label, text, score}] (점수 높은 순).

    3: 호 이름·목 시설명과 같다  2: 호 이름을 포함하거나 그 안에 든다  1: 원문 본문에 나온다
    한 글자 표현은 같을 때만 인정한다(부분 일치 오탐 방지).
    """
    q = nospace(use)
    cat = catalog(store)
    m = re.match(r"^(?:별표1)?제?(\d+(?:의\d+)?)호(?:([가-힣])목)?", q)   # 목은 가…하 다음 거·너·더… 로 이어진다
    if m:
        for c in cat:
            if c["ho"] == m.group(1):
                it = next((it for it in c["items"] if m.group(2) and it["path"] == f"{c['ho']}/{m.group(2)}"), None)
                return [{**c, **({"label": it["label"], "text": it["text"]} if it else {}), "score": 3}]
        return []
    if not q:
        return []
    hits = []
    for c in cat:
        name = nospace(c["name"])
        best = (3, c) if q == name else (0, None)
        term = None
        for it in c["items"]:
            if best[0] < 3 and q in facility_terms(it["text"]):
                best, term = (3, it), q
        if best[0] < 2 and len(q) >= 2 and (q in name or name in q):
            best = (2, c)
        if best[0] < 1 and len(q) >= 2:
            for it in c["items"]:
                if q in nospace(it["text"]):
                    best = (1, it)
                    break
        if best[0]:
            src = best[1]
            hits.append({"ho": c["ho"], "name": c["name"], "label": src["label"], "text": src["text"],
                         "score": best[0], "term": term})
    hits.sort(key=lambda h: -h["score"])
    return hits


def names(store) -> list[str]:
    """해석 실패 시 고를 수 있도록 별표 1 호 이름과 목 번호·시설명 — 일상어를 시설명에 잇고, 번호로 다시 부르게."""
    out = []
    for c in catalog(store):
        terms = [f"{it['path'].rpartition('/')[2]}목 {'·'.join(ts)}" for it in c["items"]
                 if (ts := [t for t in facility_terms(it["text"]) if t != nospace(c["name"])])]
        out.append(f"제{c['ho']}호 {c['name']}" + (f": {', '.join(terms)}" if terms else ""))
    return out
