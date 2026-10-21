"""별표 텍스트를 항목 단위로 나눈다.

계층: `1.` → `가.` → `(1)` → `(가)`. path 예 `2/차/1`, label 예 `제2호 차목 (1)`.
상위 항목의 텍스트에는 하위 항목 줄도 포함한다(검색·발췌 문맥용).
"""
import re

LEVELS = [
    (re.compile(r"^(\d+)(?:의(\d+))?\.(?:\s|(?=「))"), lambda m: m.group(1) + (f"의{m.group(2)}" if m.group(2) else ""), "제{}호"),
    (re.compile(r"^([가-하])\.(?:\s|(?=「))"), lambda m: m.group(1), "{}목"),
    (re.compile(r"^\((\d+)\)"), lambda m: m.group(1), "({})"),
    (re.compile(r"^\(([가-하])\)"), lambda m: m.group(1), "({})"),
]


def _match(line: str):
    for lv, (rx, key, fmt) in enumerate(LEVELS):
        m = rx.match(line)
        if m:
            return lv, key(m), fmt.format(key(m))
    return None


def _lines(text: str) -> list[str]:
    """번호로 시작하지 않는 줄(표 셀·고정폭 줄바꿈)은 앞 줄에 이어 붙인다."""
    lines: list[str] = []
    # 한 줄에 붙어 온 다음 목("…것 아.「건축법 시행령」…")을 떼어 낸다
    text = re.sub(r"\s([가-하])\.\s?(?=「)", r"\n\1. ", text)
    for raw in text.splitlines():
        s = re.sub(r"\s+", " ", raw).strip()
        if not s:
            continue
        if lines and not _match(s) and _match(lines[-1]) is not None:
            lines[-1] += " " + s
        else:
            lines.append(s)
    return lines


def split_annex(text: str) -> list[tuple[str, str, str]]:
    stack: list[tuple[int, str, str]] = []
    items: dict[str, tuple[str, list[str]]] = {}
    order: list[str] = []
    for line in _lines(text):
        hit = _match(line)
        if not hit:
            continue  # 제목·머리말
        lv = hit[0]
        while stack and stack[-1][0] >= lv:
            stack.pop()
        for i in range(1, len(stack) + 1):
            items["/".join(s for _, s, _ in stack[:i])][1].append(line)
        stack.append(hit)
        path = "/".join(s for _, s, _ in stack)
        if path in items:  # 같은 번호가 다시 나오면(표 안 번호 등) 뒤에 이어 붙인다
            items[path][1].append(line)
            continue
        items[path] = (" ".join(lab for _, _, lab in stack), [line])
        order.append(path)
    return [(p, items[p][0], "\n".join(items[p][1])) for p in order]
