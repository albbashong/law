"""본문 속 인용 표현을 찾아 대상 조문·별표 경로로 바꾼다.

예) "영 제84조제1항"                → rel=영, path 제84조/1
    "「건축법 시행령」 별표 1 제17호" → law=건축법 시행령, path 별표1:17
    "같은 법 시행령 제11조"          → 직전 「」 법령의 시행령
    "제11조 및 제14조"               → 접속어로 이어지면 앞 인용의 법령을 이어받음
"""
import re
from dataclasses import dataclass

LAW = r"「(?P<law>[^」]+)」\s*"
SAME = r"(?P<same>같은\s*법(?:\s*(?P<same_sfx>시행령|시행규칙))?)\s*"
SELF = r"(?P<self>이\s*(?:법|영|규칙|조례))\s*"
REL = r"(?<![가-힣])(?P<rel>법|영|규칙|시행령|시행규칙)\s*"
JO = (r"제\s*(?P<jo>\d+)\s*조(?:의\s*(?P<jo2>\d+))?(?:\s*제\s*(?P<hang>\d+)\s*항)?"
      r"(?:\s*제\s*(?P<ho>\d+)\s*호)?(?:\s*(?P<mok>[가-하])\s*목)?")
ANNEX = (r"별표\s*(?P<an>\d+)(?:\s*의\s*(?P<an2>\d+))?(?:\s*제\s*(?P<aho>\d+)\s*호)?"
         r"(?:\s*(?P<amok>[가-하])\s*목)?")
RX = re.compile(rf"(?:{LAW}|{SAME}|{SELF}|{REL})?(?:{JO}|{ANNEX})")
JOINER = re.compile(r"^\s*(?:및|또는|,|ㆍ|·|와|과|부터|까지|내지|~)?\s*$")
REL_NORM = {"시행령": "영", "시행규칙": "규칙"}


@dataclass
class Citation:
    law: str | None
    rel: str | None
    jo: str | None
    hang: str | None
    ho: str | None
    mok: str | None
    annex: str | None
    annex_item: str | None
    span: str


def find_citations(text: str, keep_internal: bool = False) -> list[Citation]:
    out: list[Citation] = []
    last_named = None          # 직전 「」 법령명 ("같은 법" 해석용)
    prev: Citation | None = None
    prev_end = -1
    for m in RX.finditer(text):
        law, rel = m.group("law"), m.group("rel")
        if law:
            law = law.strip()
            last_named = law
        elif m.group("same") and last_named:
            base = re.sub(r"\s*시행(령|규칙)$", "", last_named)
            law = base + (f" {m.group('same_sfx')}" if m.group("same_sfx") else "")
        rel = REL_NORM.get(rel, rel)
        if m.group("self"):
            if not keep_internal:
                prev, prev_end = None, m.end()
                continue
            rel = "self"
        if not (law or rel):
            if prev and JOINER.match(text[prev_end:m.start()]):
                law, rel = prev.law, prev.rel
            elif keep_internal:
                rel = "self"
            else:
                prev, prev_end = None, m.end()
                continue
        jo = None
        if m.group("jo"):
            jo = f"제{m.group('jo')}조" + (f"의{m.group('jo2')}" if m.group("jo2") else "")
        annex = None
        if m.group("an"):
            annex = m.group("an") + (f"의{m.group('an2')}" if m.group("an2") else "")
        item = "/".join(x for x in (m.group("aho"), m.group("amok")) if x) or None
        c = Citation(law, rel, jo, m.group("hang"), m.group("ho"), m.group("mok"),
                     annex, item, m.group(0))
        out.append(c)
        prev, prev_end = c, m.end()
    return out


def citation_path(c: Citation) -> str:
    if c.annex:
        return f"별표{c.annex}" + (f":{c.annex_item}" if c.annex_item else "")
    segs = [c.jo]
    if c.hang or c.ho:
        segs.append(c.hang or "-")
    if c.ho:
        segs.append(c.ho)
    if c.mok:
        segs.append(c.mok)
    return "/".join(segs)
