"""수치 뽑기·대조 — 답과 원문에서 단위별 수치를 값으로 뽑아, 표기가 달라도(5만원 = 50,000원) 같은 값이면 맞는다고 본다.

단위군: 비율(rates 모듈 — %, N분의 M, 1/N), 원, ㎡, m, 년·개월·일·시간, 층, 대.
원문의 표 칸에는 단위 없이 '100,000'처럼 적히는 일이 많아, 금액은 천 단위 쉼표가 있는 맨 숫자도 원문 값으로 친다.
날짜(2024년 8월 27일)는 기간이 아니므로 뺀다.
"""
import re

import rates

NUM = r"\d+(?:[,\u202f\u00a0]\d{3})*(?:\.\d+)?"   # 천 단위: 쉼표·좁은 공백
WON_UNIT = {"억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4, "천": 10**3, "": 1}
WON = re.compile(rf"((?:{NUM}\s*(?:억|천만|백만|만|천)\s*)*{NUM}\s*(?:억|천만|백만|만|천)?)\s*원")
WON_PART = re.compile(rf"({NUM})\s*(억|천만|백만|만|천)?")
BARE_MONEY = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])")
KNUM = rf"({NUM})\s*(만|천)?\s*"   # 1만 제곱미터, 5천㎡
SIMPLE = [
    ("㎡", re.compile(rf"{KNUM}(?:㎡|제곱미터|m2|m²)")),
    ("m", re.compile(rf"{KNUM}(?:미터|m)(?![a-zA-Z²2])")),
    ("층", re.compile(rf"({NUM})\s*층")),
    ("대", re.compile(rf"({NUM})\s*대(?![가-힣])")),
    ("개월", re.compile(rf"({NUM})\s*개월")),
    ("시간", re.compile(rf"({NUM})\s*시간")),
    ("년", re.compile(rf"({NUM})\s*년(?!\s*\d+\s*월)")),
    ("일", re.compile(rf"(?<!월\s)(?<!월)({NUM})\s*일(?:분)?")),
]


def _f(s: str) -> float:
    v = float(re.sub(r"[,\u202f\u00a0]", "", s))
    return int(v) if v.is_integer() else v


def _won(raw: str) -> float:
    return sum(_f(m.group(1)) * WON_UNIT[m.group(2) or ""] for m in WON_PART.finditer(raw))


def claims(text: str) -> list[tuple[str, str, float]]:
    """(원래 표기, 단위군, 값)."""
    text = text or ""
    out = [(t, "비율", v) for t, v in rates._hits(text)]
    for m in WON.finditer(text):
        out.append((m.group(0), "원", _won(m.group(1))))
    for unit, rx in SIMPLE:
        for m in rx.finditer(text):
            if m.start() and (text[m.start() - 1].isdigit() or text[m.start() - 1] in ",."):
                continue                                               # 숫자 중간부터 잡은 것
            v = _f(m.group(1)) * (WON_UNIT[m.group(2) or ""] if rx.groups > 1 else 1)
            if unit == "년" and isinstance(v, int) and v >= 1900:      # 연도
                continue
            out.append((m.group(0), unit, v))
    return out


def known(text: str) -> set[tuple[str, float]]:
    """원문 값 — 단위가 붙은 수치 + 쉼표 있는 맨 숫자(표 칸의 금액)."""
    vals = {(f, v) for _, f, v in claims(text)}
    vals |= {("원", _f(m.group(0))) for m in BARE_MONEY.finditer(text or "")}
    return vals


def unmatched(answer: str, source: str) -> list[str]:
    """답의 수치 표기 중 같은 단위군에서 값이 원문에 없는 것(나온 순서대로)."""
    have = known(source)
    out: list[str] = []
    for raw, fam, v in sorted(claims(answer), key=lambda c: (answer or "").find(c[0])):
        if (fam, v) not in have and raw not in out:
            out.append(raw)
    return out
