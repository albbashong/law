"""법령 XML / 조례 평문을 조·항·호·목 단위로 나눈다.

path 규칙: 조 `제84조`, 가지조 `제84조의2`, 항 `제84조/1`, 호 `제84조/1/16`, 목 `제84조/1/16/가`.
항 없이 바로 호가 오면 항 자리에 `-` (`제56조/-/16`).
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

CIRCLED = {c: i for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", 1)}
HO_RE = re.compile(r"^\s*(\d+)(?:의(\d+))?\.\s")
MOK_RE = re.compile(r"^\s*([가-하])\.\s")
HANG_RE = re.compile(r"^\s*([①-⑳])")


@dataclass
class Unit:
    path: str
    label: str
    title: str
    text: str
    parent: str | None


def _jo_key(no: str, branch: str | None) -> str:
    key = f"제{int(no)}조"
    if branch and branch.strip().isdigit() and int(branch):
        key += f"의{int(branch)}"
    return key


def _ho_no(text: str) -> str | None:
    m = HO_RE.match(text)
    if not m:
        return None
    return m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")


def _label(jo: str, hang, ho=None, mok=None) -> str:
    s = jo + (f"제{hang}항" if hang else "")
    if ho:
        s += f"제{ho}호"
    if mok:
        s += f"{mok}목"
    return s


def law_units(root: ET.Element) -> list[Unit]:
    out: list[Unit] = []
    for ju in root.iter("조문단위"):
        if ju.findtext("조문여부") != "조문":
            continue
        jo = _jo_key(ju.findtext("조문번호"), ju.findtext("조문가지번호"))
        title = (ju.findtext("조문제목") or "").strip()
        body = [(ju.findtext("조문내용") or "").strip()]
        children: list[Unit] = []
        hangs = ju.findall("항")
        for hi, h in enumerate(hangs, 1):
            mark = (h.findtext("항번호") or "").strip()[:1]
            hno = CIRCLED.get(mark) or (hi if len(hangs) > 1 else None)
            hpath = f"{jo}/{hno or '-'}"
            htext = (h.findtext("항내용") or "").strip()
            if htext:
                body.append(htext)
                children.append(Unit(hpath, _label(jo, hno), title, htext, jo))
            for ho in h.findall("호"):
                t = (ho.findtext("호내용") or "").strip()
                n = _ho_no(t) or (ho.findtext("호번호") or "").strip().rstrip(".")
                ppath = f"{hpath}/{n}"
                body.append(t)
                children.append(Unit(ppath, _label(jo, hno, n), title, t, hpath))
                for mk in ho.findall("목"):
                    mt = (mk.findtext("목내용") or "").strip()
                    m = MOK_RE.match(mt)
                    mn = m.group(1) if m else (mk.findtext("목번호") or "").strip().rstrip(".")
                    body.append(mt)
                    children.append(Unit(f"{ppath}/{mn}", _label(jo, hno, n, mn), title, mt, ppath))
        out.append(Unit(jo, jo, title, "\n".join(b for b in body if b), None))
        out.extend(children)
    return out


def ordin_units(root: ET.Element) -> list[Unit]:
    out: list[Unit] = []
    for z in root.iter("조"):
        if z.findtext("조문여부") not in ("Y", "조문"):
            continue
        raw = (z.findtext("조문번호") or "").strip()
        if not raw.isdigit() or len(raw) < 4 or int(raw[:4]) == 0:
            continue
        jo = _jo_key(raw[:4], raw[4:6])
        title = (z.findtext("조제목") or "").strip()
        text = (z.findtext("조내용") or "").strip()
        out.append(Unit(jo, jo, title, text, None))
        hang = ho = None
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            hm = HANG_RE.match(line)
            if hm:
                hang, ho = CIRCLED[hm.group(1)], None
                out.append(Unit(f"{jo}/{hang}", _label(jo, hang), title, line, jo))
                continue
            hseg = str(hang) if hang else "-"
            n = _ho_no(line)
            if n:
                ho = n
                parent = f"{jo}/{hang}" if hang else jo
                out.append(Unit(f"{jo}/{hseg}/{n}", _label(jo, hang, n), title, line, parent))
                continue
            mm = MOK_RE.match(line)
            if mm and ho:
                out.append(Unit(f"{jo}/{hseg}/{ho}/{mm.group(1)}", _label(jo, hang, ho, mm.group(1)),
                                title, line, f"{jo}/{hseg}/{ho}"))
    return out
