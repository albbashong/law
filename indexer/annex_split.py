"""별표 정리 — 목록 API 가 준 별표 행을 본문 머리말 기준으로 바로잡는다.

시군이 별표 1~25 를 파일 하나로 올린 합본은 줄머리의 '[별표 N]'·'【별표 N】'·'〔별표 N〕' 머리말(앞에
'■ ○○ 조례'가 붙기도 한다)로 쪼갠다. 머리말이 하나뿐이면 번호를 머리말에서 읽고, 목록 제목이 번호뿐일 때만
제목도 본문에서 읽는다. 괄호로 둘러싼 머리말만 본다 — 본문 속 '별표 1 제1호의 단독주택' 같은 인용은
머리말이 아니다.
"""
import re

HEAD = re.compile(r"(?m)^[ \t]*(?:■[^\n\[【〔]{0,60})?[\[【〔(]\s*별\s*표\s*(\d+)(?:\s*의\s*(\d+))?\s*[\]】〕)]")
# 연혁 표시 — <개정 2012. 11. 5.>, (개정 2006. 10. 4), (종전 별표 1의4에서 이동 2024. 1. 1) 등 법령 용어로 시작하는 괄호
NOTE = re.compile(r"[<〈][^>〉]*[>〉]|[(（\[]\s*(?:전문개정|개정|신설|삭제|종전|본조신설)[^)）\]]*[)）\]]")
BARE = re.compile(r"[\[【〔(]?\s*별\s*표\s*[\d의\s]*[\]】〕)]?|~|∼|부터|까지|\s")   # 번호·범위만 있는 목록 제목
TITLE_MAX = 60


def _no(n: str, branch: str | None) -> str:
    return str(int(n)) + (f"의{int(branch)}" if branch and branch.isdigit() and int(branch) else "")


def _title(segment: str, head_end: int, doc_name: str) -> str:
    """머리말 뒤 같은 줄, 비었으면 이어지는 줄 중 첫 내용 줄 — 연혁 표시와 조례 이름 줄은 건너뛴다."""
    skip = re.sub(r"\s+", "", doc_name)
    for line in segment[head_end:].split("\n")[:8]:
        t = re.sub(r"\s+", " ", NOTE.sub("", line)).strip()
        if t and (not skip or re.sub(r"\s+", "", t) != skip):
            return t[:TITLE_MAX]
    return ""


def _heads(text: str) -> list[tuple[int, int, str]]:
    """(시작, 머리말 끝, 번호) — 번호가 앞 머리말보다 커질 때만 새 별표로 본다."""
    out, last = [], (-1, -1)
    for m in HEAD.finditer(text):
        key = (int(m.group(1)), int(m.group(2) or 0))
        if key > last:
            out.append((m.start(), m.end(), _no(m.group(1), m.group(2))))
            last = key
    return out


def normalize_annexes(rows, doc_name: str = ""):
    """[(no, branch, title, text)] → [(번호, 제목, 텍스트)]."""
    out = []
    for no, branch, title, text in rows:
        heads = _heads(text) if text else []
        if not heads or (len(heads) == 1 and text[:heads[0][0]].strip()):
            out.append((_no(no, branch), title, text))       # 머리말이 없거나 맨 앞이 아니면 목록대로
            continue
        if len(heads) == 1:
            listed = BARE.sub("", title or "")
            hno, head_end = heads[0][2], heads[0][1]
            out.append((hno, title if len(listed) >= 4 else (_title(text, head_end, doc_name) or title), text))
            continue
        for i, (start, head_end, hno) in enumerate(heads):
            seg = text[start:heads[i + 1][0] if i + 1 < len(heads) else len(text)]
            out.append((hno, _title(seg, head_end - start, doc_name) or title, seg))
    return out
