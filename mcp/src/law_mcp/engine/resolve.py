"""사용자 표현을 DB 키로 바꾼다: 법령명 → 문서, "제84조제1항" → path, "영천" → 도시계획 조례."""
import difflib
import re

_SUFFIX = re.compile(r"\s*(시행령|시행규칙)$")
_names_cache: dict = {}


def name_key(s: str) -> str:
    return re.sub(r"[\s·ㆍ\(\)「」]", "", s or "")


def find_doc(store, name: str) -> list[dict]:
    """정확 키 → 별칭(+시행령/시행규칙 접미) → 포함 검색 순."""
    key = name_key(name)
    if not key:
        return []
    hit = store.docs_by_key(key)
    if hit:
        return hit
    m = _SUFFIX.search(name.strip())
    if m:
        base = store.docs_by_key(name_key(_SUFFIX.sub("", name.strip())))
        for b in base:
            hit = store.docs_by_key(name_key(f"{b['name']} {m.group(1)}"))
            if hit:
                return hit
    return store.docs_like(key, limit=5) if len(key) >= 3 else []


def suggest(store, name: str, n: int = 5) -> list[str]:
    names = _names_cache.get(id(store))
    if names is None:
        names = [r["name"] for r in store._all("SELECT name FROM doc")]
        _names_cache[id(store)] = names
    return difflib.get_close_matches(name, names, n=n, cutoff=0.5)


def close_name(store, name: str, ratio: float, margin: float) -> str | None:
    """못 찾은 법령명과 글자가 뚜렷이 가장 닮은 법령명 하나 — 닮음 ratio 이상, 다음 후보보다 margin 앞설 때만."""
    if ratio <= 0:
        return None
    key = name_key(name)
    if len(key) < 4:
        return None
    names = _names_cache.get(id(store))
    if names is None:
        names = [r["name"] for r in store._all("SELECT name FROM doc")]
        _names_cache[id(store)] = names
    sm, floor, scored = difflib.SequenceMatcher(), ratio - margin, []
    sm.set_seq2(key)
    for n in set(names):          # 빠른 상한으로 먼저 거른다 — 법령·조례 이름이 16만 개가 넘는다
        sm.set_seq1(name_key(n))
        if sm.real_quick_ratio() >= floor and sm.quick_ratio() >= floor and (r := sm.ratio()) >= floor:
            scored.append((r, n))
    scored = sorted(scored, reverse=True)[:2]
    if not scored or scored[0][0] < ratio:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < margin:
        return None
    return scored[0][1]


def strip_law(article: str, *names: str) -> str:
    """조문 칸 앞에 그 법령의 이름(「」 포함)이 붙었으면 뗀다 — 다른 이름이 붙은 것은 그대로 둔다(엉뚱한 법령의 조문을 열지 않게)."""
    t = re.sub(r"[\s「」]", "", article or "")
    for n in sorted({re.sub(r"[\s「」]", "", x or "") for x in names if x}, key=len, reverse=True):
        if n and t.startswith(n) and t != n:
            return t[len(n):]
    return article


def parse_article(s: str) -> str | None:
    t = re.sub(r"\s+", "", s or "")
    m = re.match(r"^별표(\d+)(?:의(\d+))?(.*)$", t)
    if m:
        no = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        rest = m.group(3)
        segs = []
        for rx in (r"제(\d+)호", r"([가-하])목", r"\((\d+)\)", r"\(([가-하])\)"):
            mm = re.search(rx, rest)
            if mm:
                segs.append(mm.group(1))
        return f"별표{no}" + (":" + "/".join(segs) if segs else "")
    m = re.match(r"^제?(\d+)조(?:의(\d+))?(?:제(\d+)항)?(?:제(\d+)호)?(?:([가-하])목)?", t)
    if not m:
        return None
    path = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    hang, ho, mok = m.group(3), m.group(4), m.group(5)
    if hang or ho:
        path += "/" + (hang or "-")
    if ho:
        path += "/" + ho
    if mok:
        path += "/" + mok
    return path


UNIT_ENDS = ("시", "군", "구")     # 시·군·구 — 지방자치법의 행정구역 단위


def _dedup(rows: list[dict]) -> list[dict]:
    return list({r["doc_id"]: r for r in reversed(rows)}.values())[::-1]


def _sidos(store, tok: str) -> list[str]:
    """줄여 쓴 시도 이름(경북·전남·세종시)을 데이터에 있는 시도 이름에 맞춘다 — 손으로 만든 줄임말 표를 두지 않는다.

    통째로 들어 있거나, 첫 글자가 같고 글자가 순서대로 들어 있는 시도 이름(경북⊂경상북도). 행정 개편으로 이름이 바뀌어도
    (전남 → 전남광주통합특별시, 광주 → 전남광주통합특별시·(구)광주광역시) 데이터의 이름을 따라간다.
    """
    def fits(name: str) -> bool:
        bare = re.sub(r"[^가-힣]", "", name)
        chars = iter(bare)
        return tok in name or (bare[:1] == tok[:1] and all(c in chars for c in tok))
    names = store.sidos()
    return [tok] if tok in names else [n for n in names if len(tok) >= 2 and fits(n)]


def find_region(store, region: str) -> list[dict]:
    """시군구 표기 여러 가지 → 도시·군계획 조례 후보 (동명이면 여러 개).

    "영천", "영천시", "경북 영천시", "수원시 장안구"(일반구), "세종시" 를 받는다.
    시도가 주어지면 다른 시도의 조례로는 절대 넘어가지 않는다(데이터에 없는 시도명은 없는 것으로 본다).
    그 시도에 항목이 없는 시·군·구(지자체 조례가 없는 행정시 등)는 시도가 지역으로 올라 있을 때 시도의 조례를 쓴다.
    """
    q = re.sub(r"\s+", " ", (region or "").strip())
    if not q:
        return []
    toks = q.split(" ")

    def names(rest: list[str]) -> list[str]:
        out = [" ".join(rest)]
        if len(rest) >= 2 and rest[-1].endswith("구") and rest[-2].endswith("시"):
            out.append(rest[-2])            # 일반구 → 그 시의 조례
        return list(dict.fromkeys(out + [rest[-1]]))

    def first(tries: list[str], sido: str | None = None) -> list[dict]:
        for t in tries:
            rows = [r for r in store.region_plan(t) if sido is None or r["sido"] == sido]
            if rows:
                return rows
        return []

    if len(toks) == 1:      # 지역 이름 그대로인 것과, 시도 이름(줄임)으로 읽은 것
        return _dedup(store.region_plan(q) + [r for s in _sidos(store, q) for r in first([s], s)])
    found, sidos = [], _sidos(store, toks[0])
    for sido in sidos:
        rest = toks[1:]
        rows = first([f"{sido} {n}" for n in names(rest)] + names(rest), sido)
        if not rows and rest[-1].endswith(UNIT_ENDS):
            rows = first([sido], sido)
        found += rows
    if found or (sidos and not store.region_plan(toks[0])):
        return _dedup(found)        # 시도가 주어졌으면 그 밖에서 찾지 않는다 — 첫 말이 시군구 이름이기도 할 때만 아래로
    return _dedup(first(names(toks)))
