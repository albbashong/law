"""조문 구조 — 나열된 호·목이 서로 어떤 관계인지를 바로 위 조항의 연결 문구(법령 용어)에서 읽는다.

"다음 각 호의 어느 하나에 해당하는"은 하나만 해당하면 되는 별개 사유, "다음 각 호의 요건을 모두"는 함께
충족할 요건, "다음 각 호의 구분에 따른"은 대상이 해당하는 호의 기준만 쓴다는 뜻이다. 모델이 긴 나열을
요약하며 이 관계를 흐리지 않도록 원문에서 읽어 사실로 싣는다.
"""
import re

from law_mcp.engine.facts import excerpt

# (연결 문구 속 법령 용어, 관계 설명) — 앞의 것이 먼저 맞는다. {k} 는 호·목
RELATIONS = (
    ("어느 하나", "각 {k} 중 하나만 해당하면 된다 — 서로 별개 사유이며 함께 충족할 필요가 없다"),
    ("모두", "각 {k}를 모두 충족해야 한다"),
    ("구분에 따", "대상이 해당하는 {k} 하나의 기준만 적용된다 — 다른 {k}의 기준을 겹쳐 쓰지 않는다"),
)
LEAD = re.compile(r"다음\s*각\s*(호|목)[^.]{0,40}")
ITEM_EXCERPT = 40
ITEMS_MAX = 30


def structure(store, doc_id: int, path: str) -> list[dict]:
    """path(와 그 아래) 조항 중 호·목을 나열한 것마다 {조항, 연결, 관계, 항목}."""
    units = store.units_with_prefix(doc_id, path)
    by_path = {u["path"]: u for u in units}
    kids: dict[str, list] = {}
    for u in units:
        parent = u["parent_path"]
        if parent is not None and parent not in by_path and parent.endswith("/-"):
            parent = parent[:-2]           # 항 번호 없는 조문 — 호의 부모는 조문 자신
        if parent in by_path and re.search(r"(호|목)$", u["label"] or ""):
            kids.setdefault(parent, []).append(u)
    out = []
    for parent, items in kids.items():
        u = by_path[parent]
        text = u["text"] or ""
        first = (items[0]["text"] or "").split("\n")[0]
        cut = text.find(first) if first else -1
        m = LEAD.search(text[:cut] if cut > 0 else text.split("\n")[0])   # 첫 호·목 앞, 이 조항 자신의 문구만
        entry = {"조항": u["label"]}
        if m:
            entry["연결"] = m.group(0).strip()
            rel = next((d for term, d in RELATIONS if term in m.group(0)), None)
            if rel:
                entry["관계"] = rel.format(k=m.group(1))
        entry["항목"] = [f"{i['label']} {excerpt(re.sub(r'^\S+\s*', '', i['text'] or ''), ITEM_EXCERPT)}"
                         for i in items][:ITEMS_MAX]
        out.append(entry)
    return out
