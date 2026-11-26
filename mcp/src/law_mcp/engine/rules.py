"""building_basis 규칙: 용도지역 정규화, 별표 항목 판정. 용도 해석은 engine/uses.py(별표 1 원문)."""
import re


def normalize_zone(zone: str) -> str:
    z = re.sub(r"\s+", "", zone or "")
    if z and not z.endswith("지역"):
        z += "지역"
    return z


def mentions(text: str, ho: str, annex_no: str) -> bool:
    """별표 텍스트가 용도 분류표(별표 {annex_no}) 제{ho}호를 가리키는가."""
    t = re.sub(r"\s+", "", text or "")
    # "제1호의2"(다른 호)는 빼고 "제1호의 단독주택"은 잡는다. "23의2"는 원문에서 "제23호의2"
    base, _, br = ho.partition("의")
    head = rf"별표{re.escape(annex_no)}제{base}호"
    return bool(re.search(rf"{head}의{br}(?!\d)" if br else rf"{head}(?!의\d)", t))


# 건축제한 별표의 대분류 머리말 — 법령 문구. "건축할 수 있는/없는 건축물", "도시ㆍ군계획조례가 정하는 바에 의하여/조례로 정하는 바에 따라"
SECTION = re.compile(r"건축할수(있는|없는)건축물")
BY_ORDINANCE = re.compile(r"조례(?:가|로)정하는바에(?:의하여|따라)")


def section_kind(header: str) -> str | None:
    """시행령·조례 건축제한 별표 대분류 머리말 → 허용|조례허용|불가|조례제한. 머리말 문구가 아니면 None(짐작하지 않는다)."""
    h = re.sub(r"\s+", "", header or "")
    h = re.sub(r"[\(\[].*", "", h)   # "(4층 이하… 조례로 따로 층수를…)" 같은 단서는 대분류 판정에서 뺀다
    m = SECTION.search(h)
    if not m:
        return None
    by_ordinance = bool(BY_ORDINANCE.search(h[:m.start()]))
    if m.group(1) == "없는":
        return "조례제한" if by_ordinance else "불가"
    return "조례허용" if by_ordinance else "허용"


def find_items(items: list[dict], ho: str, annex_no: str) -> list[dict]:
    """해당 호를 언급하는 가장 구체적인 항목들 (상위 항목에 하위 줄이 포함돼 있어 하위를 우선)."""
    hit = [i for i in items if mentions(i["text"].split("\n", 1)[0], ho, annex_no)]
    return hit


def header_of(items: list[dict], item: dict) -> str:
    top = item["path"].split("/")[0]
    for i in items:
        if i["path"] == top:
            return i["text"].split("\n", 1)[0]
    return ""


NEXT_REF = re.compile(r"(?:「[^」]*」)?(?:별표\d+)?제\d+호")     # 같은 줄의 다음 용도 인용
LIST_JOIN = re.compile(r"[,.]")                                 # 나열 — 앞뒤 용도가 따로다
PAIR_JOIN = re.compile(r"및$|와$|과$|ㆍ$")                       # 묶음 — 뒤에 붙은 한정이 앞 용도에도 걸릴 수 있다
REVISION = re.compile(r"<[^<>]*>")                              # <개정 2019. 2. 6> 같은 개정 표시
DOTS = re.compile(r"[·․‧・]")                                   # 가운뎃점의 여러 표기 → ㆍ


def _plain(text: str) -> str:
    return DOTS.sub("ㆍ", REVISION.sub("", re.sub(r"\s+", "", text or "")))


def is_partial(line: str, ho: str, annex_no: str, name: str) -> bool:
    """별표 항목이 그 용도 전체가 아니라 일부 유형·조건만 가리키는가 — 낱말 목록이 아니라 구조로 본다.

    호 인용 뒤, 같은 줄의 다음 인용(또는 끝)까지에 그 용도의 이름(name, 용도 분류표의 호 이름)만 있으면 전체다.
    그 밖의 말(목 지정·"으로서 …"·괄호 단서·더 좁은 시설 이름)이 있으면 일부다.
      "바. 「건축법 시행령」 별표 1 제14호의 업무시설"         → 전체(False)
      "자. … 제17호의 공장으로서 … 지역에 설치하는 것"        → 일부(True)
    이름도 이음말도 없이 곧바로 다음 인용이 오면("제21호의 제21호가목에 따른 …") 그 인용이 이 호를 좁히는 것이라 일부다.
    "및"으로 묶인 뒤 용도에 괄호 단서가 있으면 그 단서가 이 용도에도 걸릴 수 있어 일부로 본다.
    """
    t = _plain(line)
    base, _, br = ho.partition("의")
    head = rf"별표{re.escape(annex_no)}제{base}호"
    m = re.search(rf"{head}의{br}(?!\d)" if br else rf"{head}(?!의\d)", t)
    if not m:
        return True
    rest = t[m.end():]
    nxt = NEXT_REF.search(rest)
    seg = re.sub(r"^의", "", rest[:nxt.start()] if nxt else rest)
    key = _plain(name)
    named = bool(key) and seg.startswith(key)
    if named:
        seg = seg[len(key):]
    paired = bool(PAIR_JOIN.search(seg))
    left = LIST_JOIN.sub("", PAIR_JOIN.sub("", seg))
    if left:
        return True
    if nxt and not named and not paired and seg == "":
        return True         # 이름도 이음말도 없이 이어진 인용 — 이 호를 좁히는 말이다
    return paired and bool(nxt) and bool(re.search(r"[\(\[]", rest[nxt.start():]))


def is_partial_item(items: list[dict], item: dict, ho: str, annex_no: str, name: str) -> bool:
    """항목이 그 용도의 일부만 가리키는가 — 하위 항목으로 대상을 열거하거나(구조), 항목 문장이 한정하거나."""
    if any(i["path"].startswith(item["path"] + "/") for i in items):
        return True
    return is_partial(item["text"].split("\n", 1)[0], ho, annex_no, name)


def subtype_cover(line: str, ho: str, mok: str | None, term: str | None) -> bool | None:
    """사용자가 목 단위로 물었을 때(일반음식점 = 제4호 자목) 항목이 그 목을 포함하는가.

    True: 그 목을 명시하거나("제4호아목ㆍ자목"), 제외 단서가 그 목을 빼지 않음("(단란주점을 제외한다)")
    False: 명시한 목에 없거나 제외 단서가 그 목을 뺌("[같은 호 아목, 자목 … 은 제외한다]")
    None: 목 단위로 판단할 근거가 없음 — 호 단위 판정(is_partial_item)을 쓴다
    """
    if not mok:
        return None
    t = re.sub(r"\s+", "", line or "")
    base, _, br = ho.partition("의")
    m = re.search(rf"제{base}호(?:의{br})?((?:[가-하]목(?:\([^)]*\))?(?:ㆍ|·|,|및)?)+)" if br else
                  rf"제{base}호((?:[가-하]목(?:\([^)]*\))?(?:ㆍ|·|,|및)?)+)", t)
    if m:
        return mok in re.findall(r"([가-하])목", m.group(1))
    segs = re.findall(r"\[([^\]]*제외[^\]]*)\]", t) + re.findall(r"\(([^()]*제외[^()]*)\)", t)
    if any(f"{mok}목" in s or (term and term in s) for s in segs):
        return False
    rest = t
    for s in segs:
        rest = rest.replace(s, "")
    if segs or (term and term in rest):
        return True     # 제외 단서가 그 목을 빼지 않았거나, 한정 문구가 그 시설명을 직접 부른다
    return None
