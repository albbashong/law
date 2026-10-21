"""building_basis 규칙: 용도지역 정규화, 별표 항목 판정. 용도 해석은 engine/uses.py(별표 1 원문)."""
import re


def normalize_zone(zone: str) -> str:
    z = re.sub(r"\s+", "", zone or "")
    if z and not z.endswith("지역"):
        z += "지역"
    return z


def mentions(text: str, ho: str) -> bool:
    """별표 텍스트가 건축법 시행령 별표 1 제{ho}호를 가리키는가."""
    t = re.sub(r"\s+", "", text or "")
    # "제1호의2"(다른 호)는 빼고 "제1호의 단독주택"은 잡는다. "23의2"는 원문에서 "제23호의2"
    base, _, br = ho.partition("의")
    rx = rf"별표1제{base}호의{br}(?!\d)" if br else rf"별표1제{base}호(?!의\d)"
    return bool(re.search(rx, t))


def section_kind(header: str) -> str:
    """시행령·조례 건축제한 별표 대분류 머리말 → 허용|조례허용|불가|조례제한."""
    h = re.sub(r"\s+", "", header or "")
    h = re.sub(r"\(.*", "", h)   # "(4층 이하… 조례로 따로 층수를…)" 같은 단서는 대분류 판정에서 뺀다
    by_ordinance = "조례" in h
    if "없는" in h:
        return "조례제한" if by_ordinance else "불가"
    return "조례허용" if by_ordinance else "허용"


def find_items(items: list[dict], ho: str) -> list[dict]:
    """해당 호를 언급하는 가장 구체적인 항목들 (상위 항목에 하위 줄이 포함돼 있어 하위를 우선)."""
    hit = [i for i in items if mentions(i["text"].split("\n", 1)[0], ho)]
    return hit


def header_of(items: list[dict], item: dict) -> str:
    top = item["path"].split("/")[0]
    for i in items:
        if i["path"] == top:
            return i["text"].split("\n", 1)[0]
    return ""


_QUALIFIERS = ("중", "으로서", "로서", "어느", "다만", "제외", "것", "해당", "외의", "이상", "이하", "미만", "초과")


def is_partial(line: str, ho: str) -> bool:
    """별표 항목이 그 용도 전체가 아니라 일부 유형·조건만 가리키는가.

    "바. 「건축법 시행령」 별표 1 제14호의 업무시설"         → 전체(False)
    "자. … 제17호의 공장으로서 … 지역에 설치하는 것"        → 일부(True)
    """
    base, _, br = ho.partition("의")
    t = re.sub(r"\s+", " ", line)
    m = re.search(rf"제\s*{base}\s*호(?:의\s*{br})?(?:의)?\s*(.*)$" if br else rf"제\s*{base}\s*호(?:의)?\s*(.*)$", t)
    rest = m.group(1) if m else t
    if re.match(r"^[가-하]\s*목", rest) or "(" in rest or "[" in rest:   # "제4호아목ㆍ자목", "제4호더목의 …"
        return True
    words = rest.rstrip(". ").split(" ")
    return len(words) > 1 and any(q in " ".join(words[1:]) or words[0].endswith(q) for q in _QUALIFIERS) \
        or any(rest.split(" ")[0].endswith(s) for s in ("으로서", "로서"))


def is_partial_item(items: list[dict], item: dict, ho: str) -> bool:
    """항목이 그 용도의 일부만 가리키는가 — 하위 항목으로 대상을 열거하거나(구조), 항목 문장이 한정하거나."""
    if any(i["path"].startswith(item["path"] + "/") for i in items):
        return True
    return is_partial(item["text"].split("\n", 1)[0], ho)


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
