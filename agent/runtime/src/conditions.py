"""조건식 대조 — 답과 조회 원문에서 "수치·단위·비교어"를 조건식으로 바꿔, 같은 (값, 단위)에 대해 답이 원문과 반대
방향으로 쓴 것을 찾는다.

조건 하나의 부호 = 방향 × 효과. 방향은 비교어(이상·초과·넘는 = +1, 이하·미만·못 미치는 = -1), 효과는 그 항목 문장과
딸린 윗단 문장("다음 각 호 …")에 든 부정(없다·외에는·제외·금지·아니한다…) 개수의 홀짝(짝수 +1, 홀수 -1)이다.
"2천㎡를 넘으면 분할할 수 있다"(+1 × +1)와 "2천㎡ 이하면 분할할 수 없다"(-1 × -1)는 같은 부호다.
비교어·부정어는 법령 용어라 바뀌지 않는다 — 규칙은 이 용어에만 기대고 분야 낱말은 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

UNITS = {"제곱미터": "㎡", "㎡": "㎡", "m²": "㎡", "m2": "㎡", "퍼센트": "%", "%": "%", "미터": "m", "m": "m"}
UP = r"이상|초과|넘[는게도을어으]|넘도록|넘는"
DOWN = r"이하|미만|못\s*미치|미달"
NUMBER = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>천|만)?\s*(?P<unit>제곱미터|㎡|m²|m2|퍼센트|%|미터|m(?![a-zA-Z²2]))"
    r"\s*(?:을|를|이|가|에|의|으로|로|은|는)?\s*(?P<cmp>" + UP + "|" + DOWN + ")")
NEGATION = re.compile(r"없[다음는습으어]|불가|금지|아니[하한함합]|않[는다음아습았]|못\s*하|제외|외에는|외의|제한(?:한|하|할|된|되|함|됨)")
# 효과를 말하는 표현 — 부정이 없어도 이것이 있으면 효과가 정해진 조건이다. 둘 다 없으면 값만 적은 것(표의 기준값 등)이라 보지 않는다
EFFECT = re.compile(r"할\s*수\s*있|받아야|받을\s*것|가능|허용|해야|하여야")
PAREN = re.compile(r"\([^()]*\)")    # 괄호 안 단서(…는 제외한다)는 적용 범위이지 효과가 아니다
ITEM = re.compile(r"(?:^|(?<=\s))(?:\d{1,2}[.)]|[가-하]\.|[①-⑳]|[-*•])\s")
LEAD = re.compile(r"다음")
SCALE = {"천": 1000, "만": 10000}


@dataclass(frozen=True)
class Condition:
    value: float
    unit: str
    sign: int
    text: str
    known: bool = True       # 항목·윗단 문장에 효과 표현이 있어 부호가 정해졌는가


def _segments(text: str) -> list[tuple[bool, str]]:
    """(항목인가, 문장) — 줄·표 칸 구분·'›'·문장 끝(다.)·항목 표지(1. 가. ① 1) - *)에서 끊는다."""
    text = re.sub(r"\*\*|__|<[^>]*>", "", text)
    out = []
    for line in re.split(r"\n|›|\|", text):
        for part in re.split(r"(?<=다\.)\s+|(?<=:)\s+", line):
            starts = [m.start() for m in ITEM.finditer(part)] or []
            cuts = sorted(set([0] + starts + [len(part)]))
            for a, b in zip(cuts, cuts[1:]):
                seg = part[a:b].strip()
                if seg:
                    out.append((bool(ITEM.match(seg)), seg))
    return out


def parse(text: str) -> list[Condition]:
    out, lead_neg, lead_effect = [], 0, False
    for is_item, seg in _segments(text):
        bare = PAREN.sub("", re.sub(DOWN, "", seg))
        lead = LEAD.search(bare)
        if lead:   # 윗단 문장의 효과는 "다음 각 …" 뒤 술어가 정한다 — 앞쪽 적용 범위(…받지 아니하거나)의 부정은 효과가 아니다
            tail = bare[lead.start():]
            if NEGATION.search(tail) or EFFECT.search(tail):
                bare = tail
        negs = len(NEGATION.findall(bare))
        effect = bool(negs or EFFECT.search(bare))
        if lead:                        # 글머리표가 붙어 있어도 "다음 …"으로 목록을 여는 문장은 윗단이다
            is_item, lead_neg, lead_effect = False, negs, effect
        elif not is_item:
            lead_neg, lead_effect = 0, False
        for m in NUMBER.finditer(seg):
            value = float(m["num"].replace(",", "")) * SCALE.get(m["scale"] or "", 1)
            direction = 1 if re.match(UP, m["cmp"]) else -1
            parity = negs + (lead_neg if is_item else 0)
            out.append(Condition(value, UNITS[m["unit"]], direction * (-1 if parity % 2 else 1), seg,
                                 effect or (is_item and lead_effect)))
    return out


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return [value]
        return _texts(data) if not isinstance(data, str) else [data]
    if isinstance(value, dict):
        if isinstance(value.get("딸린_문장"), list):   # 호·목과 그것이 딸린 윗단 문장을 한 글로 — 효과는 윗단이 정한다
            body = next((v for k, v in value.items() if isinstance(v, str) and k not in ("조문", "제목")), "")
            rest = {k: v for k, v in value.items() if k != "딸린_문장" and v != body}
            return ["\n".join([*value["딸린_문장"], body])] + _texts(rest)
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def reversed_conditions(answer: str, messages: list[dict[str, Any]]) -> list[str]:
    """답의 조건 가운데 같은 (값, 단위)의 원문(도구 결과) 조건 어느 것과도 부호가 맞지 않는 것 → "답 … ↔ 원문 …"."""
    sources: dict[tuple[float, str], list[Condition]] = {}
    for m in messages:
        if m.get("role") == "tool":
            for t in _texts(m.get("content")):
                for c in parse(t):
                    if c.known:
                        sources.setdefault((c.value, c.unit), []).append(c)
    out = []
    for c in parse(answer):
        known = sources.get((c.value, c.unit)) if c.known else None
        if known and all(k.sign != c.sign for k in known):
            line = f"“{c.text[:80]}” ↔ 원문 “{known[0].text[:80]}”"
            if line not in out:
                out.append(line)
    return out
