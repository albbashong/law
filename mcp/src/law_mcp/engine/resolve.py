"""사용자 표현을 DB 키로 바꾼다: 법령명 → 문서, "제84조제1항" → path, "영천" → 도시계획 조례."""
import difflib
import re

SIDO_SHORT = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}
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


SIDO_SUFFIX = ("도", "특별시", "광역시", "특별자치시", "특별자치도")


def find_region(store, region: str) -> list[dict]:
    """시군구 표기 여러 가지 → 도시·군계획 조례 후보 (동명이면 여러 개).

    "영천", "영천시", "경북 영천시", "수원시 장안구"(일반구), "세종시" 를 받는다.
    시도가 주어지면 다른 시도의 조례로는 절대 넘어가지 않는다(데이터에 없는 시도명은 없는 것으로 본다).
    """
    q = re.sub(r"\s+", " ", (region or "").strip())
    if not q:
        return []
    toks = q.split(" ")
    if toks[0] in SIDO_SHORT:
        toks[0] = SIDO_SHORT[toks[0]]
    if toks[0] in ("세종시", "세종"):
        toks[0] = "세종특별자치시"
    sido = toks[0] if toks[0].endswith(SIDO_SUFFIX) and len(toks[0]) > 2 else None
    rest = toks[1:] if sido else toks
    if sido and rest and not store.sido_known(sido):
        sido = None     # 데이터에 없는 시도명(통합·개편 전 이름 등) — 시군구로만 찾는다
    names = []
    if rest:
        names.append(" ".join(rest))
        if len(rest) >= 2 and rest[-1].endswith("구") and rest[-2].endswith("시"):
            names.append(rest[-2])          # 일반구 → 그 시의 조례
        names.append(rest[-1])
    tries = ([f"{sido} {n}" for n in names] + names) if sido else names
    if sido and not rest:
        tries = [sido]
    for t in dict.fromkeys(tries):
        rows = store.region_plan(t)
        if sido:
            rows = [r for r in rows if r["sido"] == sido]
        if rows:
            seen, out = set(), []
            for r in rows:
                if r["doc_id"] not in seen:
                    seen.add(r["doc_id"])
                    out.append(r)
            return out
    return []
