"""법령체계도(법제처 lsStmd) XML 에서 법령의 상하위를 읽는다 — 법령 이름 꼴로 짐작하지 않는다.

체계도는 법률 아래에 시행령, 그 아래에 시행규칙이 요소로 겹쳐 있다. 행정규칙·자치법규 가지는 보지 않는다.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass

TIERS = ("법률", "시행령", "시행규칙")   # 체계도 XML 의 법령 단계 요소 이름(법제처 형식)


@dataclass(frozen=True)
class Node:
    law_id: str
    tier: str
    parent: str | None      # 바로 위 법령의 법령ID
    name: str = ""


def law_nodes(xml: str) -> list[Node]:
    out = []

    def walk(el, parent):
        info = el.find("기본정보")
        law_id = (info.findtext("법령ID") or "").strip() if info is not None else ""
        if law_id:
            out.append(Node(law_id, el.tag, parent, (info.findtext("법령명") or "").strip()))
        for c in el:
            if c.tag in TIERS:
                walk(c, law_id or parent)   # 자리만 있는 단계(정보 없음)는 건너 그 위에 잇는다

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for top in root.iter("상하위법"):
        for c in top:
            if c.tag in TIERS:
                walk(c, None)
    return out


def relatives(nodes: dict) -> dict:
    """법령ID → {'법': 법령ID, '영': …, '규칙': …} — 그 법령 본문의 '법'·'영'·'규칙'(또는 "같은 법 시행령")이 가리키는 법령.

    위로는 조상에서, 자기 단계는 자기 자신. 아래로는 체계도에서 그 법령 밑에 있는 그 단계의 법령 가운데 — 하나뿐이면 그것,
    여럿이면(건축법 시행령 밑의 규칙 다섯 개) 이름이 "법률 이름 + 시행령/시행규칙"인 것. 그런 것이 없으면 정하지 않는다.
    """
    abbr = dict(zip(TIERS, ("법", "영", "규칙")))
    kids = {}
    for n in nodes.values():
        if n.parent:
            kids.setdefault(n.parent, []).append(n)

    def below(law_id):
        out, layer, seen = [], list(kids.get(law_id, [])), {law_id}
        while layer:
            layer = [k for k in layer if k.law_id not in seen]
            seen.update(k.law_id for k in layer)
            out += layer
            layer = [g for k in layer for g in kids.get(k.law_id, [])]
        return out

    key = lambda s: "".join((s or "").split())
    out = {}
    for n in nodes.values():
        m = {abbr[n.tier]: n.law_id}
        up, seen = n, {n.law_id}
        while up.parent and up.parent in nodes and up.parent not in seen:
            up = nodes[up.parent]
            seen.add(up.law_id)
            m.setdefault(abbr[up.tier], up.law_id)
        act = nodes[m["법"]].name if "법" in m else ""
        by_tier = {}
        for k in below(n.law_id):
            by_tier.setdefault(k.tier, []).append(k)
        for tier, ks in by_tier.items():
            if abbr[tier] in m:
                continue
            named = [k for k in ks if act and key(k.name) == key(f"{act} {tier}")]
            pick = ks if len(ks) == 1 else named
            if len(pick) == 1:
                m[abbr[tier]] = pick[0].law_id
        out[n.law_id] = m
    return out
