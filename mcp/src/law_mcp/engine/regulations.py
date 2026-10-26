"""필지에 걸린 다른 규제(개발제한구역·접도구역 등)의 근거 조문을 법령 데이터에서 찾는다 — 규제별 표를 두지 않는다.

토지이용계획의 규제 이름은 그 구역을 정한 법령의 조문 제목에 그대로 나온다("접도구역" → 도로법 제40조(접도구역의 지정 및 관리)).
  1. 법령 조문 제목에 그 이름이 든 것 — 법령 이름에도 그 말이 든 법령(개발제한구역법) 먼저, 그다음 상위 법령·많이 인용되는 법령
  2. 제목에 없으면 그 이름을 정의하는 조문 — "준보전산지: 보전산지 외의 산지"(산지관리법 제4조), "…(이하 "X"라 한다)"
  3. 그래도 없으면 본문 검색의 첫 결과
도로·공원처럼 조문 제목에 흔한 말은 어느 법령의 규제인지 가릴 수 없어 붙이지 않는다.
"""
import re

from law_mcp.config import get_settings
from law_mcp.engine.follow import _cited_by, rank
from law_mcp.store.db import nospace

PER_NAME = 2
_cache: dict = {}


def base_name(name: str) -> str:
    """'가축사육제한구역(전부제한지역)' → '가축사육제한구역', '도로(저촉)' → '도로'."""
    return re.sub(r"\s*\(.*?\)\s*", "", name or "").strip()


def _by_title(store, key: str) -> list[dict]:
    hits = [h for h in store.search_titles([key], source="law", limit=60) if h["kind"] == "u"]
    rows = []
    for h in hits:
        u = store._one("SELECT u.label, u.title, u.text, d.doc_id, d.name, d.name_key, d.kind FROM unit u"
                       " JOIN doc d ON d.doc_id=u.doc_id WHERE u.unit_id=?", (h["ref"],))
        if u and key in nospace(u["title"] or ""):
            rows.append(u)
    rows.sort(key=lambda u: (key not in (u["name_key"] or ""), rank(u["kind"]), -_cited_by(store, u["doc_id"])))
    return [{"law": u["name"], "article": u["label"], "title": u["title"] or "", "text": u["text"] or ""} for u in rows[:PER_NAME]]


def _defines(text: str, base: str) -> bool:
    """본문이 그 이름을 정의하는가 — 정의 형식은 법령 문체(쌍점 나열·"이란"·"이하 …라 한다")에서 온다."""
    w = r"\s*".join(map(re.escape, base.replace(" ", "")))
    return bool(re.search(rf"(^|\n)\s*\d+\.\s*{w}\s*[:：]|[“\"]{w}[”\"]\s*(이)?란|이하\s*[“\"]{w}[”\"]", text or ""))


def _by_definition(store, base: str) -> list[dict]:
    rows = [u for u in store.search_units(f'"{nospace(base)}"', source="law", limit=300) if _defines(u["text"], base)]
    rows.sort(key=lambda u: (rank(store.doc(u["doc_id"])["kind"]), -_cited_by(store, u["doc_id"])))
    out = []
    for u in rows:
        jo = store.unit(u["doc_id"], u["path"].split("/", 1)[0]) or u
        item = {"law": u["law"], "article": jo["label"], "title": jo["title"] or "", "text": u["text"] or ""}
        if all(o["article"] != item["article"] or o["law"] != item["law"] for o in out):
            out.append(item)
    return out[:PER_NAME]


def _by_body(store, name: str) -> list[dict]:
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, name, scope="law", limit=PER_NAME)
    return [{"law": x["law"], "article": x["article"], "title": x.get("title", ""), "text": x.get("snippet", "")}
            for x in (r.get("facts") or {}).get("결과", [])[:PER_NAME] if isinstance(x, dict) and x.get("law")]


def regulation_basis(store, name: str) -> list[dict]:
    """규제 이름 하나의 근거 조문 [{law, article, title, text}] — 못 찾으면 []."""
    base = base_name(name)
    key = nospace(base)
    if len(key) < 2 or store.title_df(key) > get_settings().regulation_title_df_max:
        return []
    ck = (id(store), key)
    if ck not in _cache:
        _cache[ck] = _by_title(store, key) or _by_definition(store, base) or _by_body(store, base)
    return _cache[ck]
