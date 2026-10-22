"""비율 수치 검증 — 답에 쓴 비율이 이번 조회 원문(도구 결과·사용자 발화)의 비율 값과 맞는지 값으로 대조한다.

법령은 비율을 'N분의 M'(1천분의 2.5, 100분의 20), 백분율(0.25%), 분수(1/40)로 쓴다. 표기가 달라도 값이
같으면 맞는 것으로 본다 — '1천분의 2.5'를 0.25%로 환산하면 통과하고 '1/2.5(0.4%)'로 옮기면 걸린다.
"""
import re
from typing import Any

NUM = r"\d+(?:[,\u202f\u00a0]\d{3})*(?:\.\d+)?"   # 천 단위: 쉼표·좁은 공백
MULT = {"": 1, "십": 10, "백": 100, "천": 1000, "만": 10000}
FRACTION = re.compile(rf"({NUM})\s*(십|백|천|만)?\s*분의\s*({NUM})")
PERCENT = re.compile(rf"({NUM})\s*(?:%|퍼센트|％)")
SLASH = re.compile(rf"(?<![\d./])({NUM})\s*/\s*({NUM})(?![\d/])")


def _f(s: str) -> float:
    return float(re.sub(r"[,\u202f\u00a0]", "", s))


def _hits(text: str) -> list[tuple[str, float]]:
    """(원래 표기, 값) — 날짜(2024/8/27 등)처럼 1 을 넘는 분수는 비율이 아니므로 뺀다."""
    out = []
    for m in FRACTION.finditer(text):
        den = _f(m.group(1)) * MULT[m.group(2) or ""]
        if den:
            out.append((m.group(0), _f(m.group(3)) / den))
    for m in PERCENT.finditer(text):
        out.append((m.group(0), _f(m.group(1)) / 100))
    for m in SLASH.finditer(text):
        a, b = _f(m.group(1)), _f(m.group(2))
        if b and a / b <= 1:
            out.append((m.group(0).replace(" ", ""), a / b))
    return [(t, round(v, 9)) for t, v in out]


def values(text: str) -> set[float]:
    return {v for _, v in _hits(text or "")}


def unmatched(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답의 비율 표기 중 값이 조회 원문 어디에도 없는 것(나온 순서대로)."""
    known: set[float] = set()
    for m in messages:
        if m.get("role") in ("tool", "user") and m.get("content") not in skip:
            known |= values(str(m.get("content") or ""))
    out: list[str] = []
    for text, v in sorted(_hits(answer or ""), key=lambda h: (answer or "").find(h[0])):
        if v not in known and text not in out:
            out.append(text)
    return out
