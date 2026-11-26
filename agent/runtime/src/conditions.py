"""조건 대조 — 답과 조회 원문이 같은 (값, 단위)를 서로 다른 비교어로 적은 곳을 찾는다.

비교어(이상·초과·넘는 ↔ 이하·미만·못 미치는)와 단위는 법령 용어라 바뀌지 않는다. 여기서는 그 둘만 읽고, 문장의 효과
("할 수 있다·없다")는 읽지 않는다 — 부정 낱말을 세어 뜻을 판정하면 말투 하나에 결과가 뒤집힌다. 비교어가 엇갈린 곳은
'의심 후보'로 답 점검 단계에 넘기고, 같은 뜻인지는 점검이 원문을 보고 가린다.
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
SCALE = {"천": 1000, "만": 10000}
NEAR = 50          # 조건 앞뒤로 함께 보일 글자 수
SENTENCE = re.compile(r"\n|\||(?<=다\.)\s+")


@dataclass(frozen=True)
class Condition:
    value: float
    unit: str
    up: bool          # 비교어의 방향 — 이상·초과면 참
    text: str         # 그 조건의 앞뒤 글


def parse(text: str) -> list[Condition]:
    out = []
    for seg in SENTENCE.split(text or ""):
        for m in NUMBER.finditer(seg):
            value = float(m["num"].replace(",", "")) * SCALE.get(m["scale"] or "", 1)
            near = seg[max(0, m.start() - NEAR):m.end() + NEAR].strip()     # 긴 조문에서도 그 조건이 보이게 앞뒤만
            out.append(Condition(value, UNITS[m["unit"]], bool(re.match(UP, m["cmp"])), near))
    return out


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return [value]
        return _texts(data) if not isinstance(data, str) else [data]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def suspects(answer: str, messages: list[dict[str, Any]]) -> list[str]:
    """답의 조건 가운데, 같은 (값, 단위)를 원문(도구 결과)은 모두 반대쪽 비교어로 적은 것 → "답 … ↔ 원문 …"."""
    sources: dict[tuple[float, str], list[Condition]] = {}
    for m in messages:
        if m.get("role") == "tool":
            for t in _texts(m.get("content")):
                for c in parse(t):
                    sources.setdefault((c.value, c.unit), []).append(c)
    out = []
    for c in parse(answer):
        known = sources.get((c.value, c.unit))
        if known and all(k.up != c.up for k in known):
            line = f"“{c.text}” ↔ 원문 “{known[0].text}”"
            if line not in out:
                out.append(line)
    return out
