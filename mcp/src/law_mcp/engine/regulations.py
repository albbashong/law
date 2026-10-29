"""필지에 걸린 다른 규제(개발제한구역·접도구역 등)의 근거 조문을 법령 데이터에서 찾는다 — 규제별 표를 두지 않는다.

  0. 다른 법령이 그 이름을 쓸 때 "「○○법」 제N조에 따른 이름"으로 가장 많이 인용하는 조문 — 그 규제를 정한 조문이다
     (조 제목 일치만으로는 "도시지역" → 적용 배제 조문, "토지거래계약에 관한 허가구역" → 다른 법의 의제 조문이 붙었다)
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
CITE_SCAN = 2000     # 인용을 세려고 읽는 조문 수 상한
# 「법령명」 제N조(제N항·제N호·N목)에 따른 / 에 따라 지정된 / 에서 정한 / 의 — 법령 문체의 인용 꼴
BORROW = (r"「([^」]{2,60})」\s*(제\s*\d+\s*조(?:의\s*\d+)?)(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?(?:\s*[가-하]목)?"
          r"\s*(?:에\s*따라\s*(?:지정|고시|결정)된|에\s*따른|에서\s*정한|의)\s*")
_cache: dict = {}


def base_name(name: str) -> str:
    """'가축사육제한구역(전부제한지역)' → '가축사육제한구역', '도로(저촉)' → '도로'."""
    return re.sub(r"\s*\(.*?\)\s*", "", name or "").strip()


def _cited_for(text: str, base: str) -> list[tuple[str, str]]:
    """본문에서 그 이름 앞에 붙은 인용 [(법령명, 제N조)]."""
    w = r"\s*".join(map(re.escape, base.replace(" ", "")))
    return [(m.group(1), re.sub(r"\s+", "", m.group(2))) for m in re.finditer(BORROW + w, text or "")]


def _tails(name: str, least: int = 4) -> list[tuple[str, str]]:
    """[(떼어 낸 앞부분, 뒷부분)] — 이름 그대로부터, 앞부분이 2글자 이상인 것만. "수질보전특별대책지역"은 다른 법령에서
    "특별대책지역"으로 인용된다."""
    n = nospace(name)
    return [("", n)] + [(n[:i], n[i:]) for i in range(2, len(n) - least + 1)]


def _by_citation(store, base: str) -> list[dict]:
    """다른 법령이 그 이름을 쓸 때 가장 많이 인용하는 조문. 이름 그대로의 인용이 없으면 이름의 뒷부분으로 세되,
    떼어 낸 앞부분이 같은 조문에 함께 나오는 것만 센다(다른 구역의 같은 뒷말을 세지 않게)."""
    counts: dict = {}
    for head, name in _tails(base):
        if len(name) < 3:               # 전문 검색(trigram)은 3글자부터
            break
        for u in store.search_units(f'"{name}"', source="law", limit=CITE_SCAN if not head else CITE_SCAN // 4):
            if head and head not in nospace(u["text"] or ""):
                continue
            for key in _cited_for(u["text"], name):
                counts[key] = counts.get(key, 0) + 1
        if counts:
            break
    out = []
    for (law, article), _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        d = store.doc_named(law)
        u = store.unit(d["doc_id"], article) if d else None
        if u:
            out.append({"law": d["name"], "article": u["label"], "title": u["title"] or "", "text": u["text"] or ""})
        if len(out) == PER_NAME:
            break
    return out


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


ACTS = re.compile(r"행위|금지|제한")                  # 구역 안에서 하지 못하는 일을 정한 조문 제목의 법률 용어
HEAD = re.compile(r"(?:의|에\s*관한)?\s*(?:지정|설정|구분|결정)(?:\s*(?:등|및.*))?$")   # 지정 조문 제목의 꼬리


def _acts(title: str, *names: str) -> bool:
    """구역 이름을 뺀 제목에 행위·금지·제한이 있는가 — "개발제한구역"처럼 이름에 든 "제한"에 속지 않게."""
    t = nospace(title or "")
    for n in names:
        t = t.replace(nospace(n), "") if n else t
    return bool(ACTS.search(t))


def restriction_title_match(titles: list[tuple[str, str]], base: str, designation_title: str) -> str | None:
    """같은 법 조문 제목 [(조, 제목)] 가운데 그 구역의 행위 제한 조문 — 규제 이름, 또는 지정 조문 제목의 구역 이름이
    행위·금지·제한과 함께 든 것."""
    zone = nospace(HEAD.sub("", designation_title or ""))
    for name in (nospace(base), zone):
        if len(name) < 2:
            continue
        for jo, title in titles:
            if name in nospace(title or "") and _acts(title, base, zone) and nospace(title) != nospace(designation_title):
                return jo
    return None


def _restriction(store, item: dict, base: str) -> dict | None:
    d = store.doc_named(item["law"])
    if not d or _acts(item["title"], base):
        return None
    titles = [(u["path"], u["title"]) for u in store._all(
        "SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id", (d["doc_id"],))]
    jo = restriction_title_match(titles, base, item["title"])
    u = store.unit(d["doc_id"], jo) if jo else None
    return ({"law": d["name"], "article": u["label"], "title": u["title"] or "", "text": u["text"] or "", "제한": True}
            if u else None)


def regulation_basis(store, name: str) -> list[dict]:
    """규제 이름 하나의 근거 조문 [{law, article, title, text}] — 못 찾으면 []."""
    base = base_name(name)
    key = nospace(base)
    if len(key) < 2 or store.title_df(key) > get_settings().regulation_title_df_max:
        return []
    ck = (id(store), key)
    if ck not in _cache:
        found = (_by_citation(store, base) or _by_title(store, key) or _by_definition(store, base)
                 or _by_body(store, base))
        extra = _restriction(store, found[0], base) if found else None    # 그 구역에서 금지·제한하는 행위
        if extra and all((x["law"], x["article"]) != (extra["law"], extra["article"]) for x in found):
            found = [*found, extra]
        _cache[ck] = found
    return _cache[ck]
