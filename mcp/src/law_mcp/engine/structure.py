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


LEAD_CHARS = 300   # 딸린 문장 한 줄 길이 상한 — 요건은 대개 문장 앞부분에 있다
AMENDED = re.compile(r"\s*<(?:개정|신설|전문개정|본조신설|타법개정)[^>]*>")
TITLE_LINE = re.compile(r"^\s*제\s*\d+\s*조(?:의\s*\d+)?\s*\([^)]*\)\s*")


def leads(store, doc_id: int, path: str, focus: str | None = None) -> list[str]:
    """path 의 호·목이 딸린 상위 항·호의 문장들(위에서 아래로) — "제84조제1항: ①…".

    focus 가 있으면(조 전체가 걸린 검색 결과) path 아래에서 그 낱말이 든 가장 깊은 조항을 기준으로 삼는다.
    조문 자신(제목 줄)은 넣지 않는다 — 요건은 항·호의 문장에 있다. 다만 항 없이 조 본문이 목록을 여는 조(제목 뒤에
    곧바로 "…란 다음 각 호의 행위를 말한다")는 그 본문 첫머리를 넣는다 — 빠지면 그 아래 호·목이 무엇의 목록(허가
    대상인지, 허가 없이 되는 것인지)인지 모른다(실측 2026-10-29 국토계획법 시행령 제53조). 개정 표시는 뺀다.
    """
    target = path
    if focus:
        key = re.sub(r"\s+", "", focus)
        deep = [u for u in store.units_with_prefix(doc_id, path) if key in re.sub(r"\s+", "", u["text"] or "")]
        if deep:
            target = max(deep, key=lambda u: u["path"].count("/"))["path"]
    out = []
    cur = store.unit(doc_id, target)
    while cur and cur["parent_path"]:
        parent = cur["parent_path"]
        if parent.endswith("/-") or "/" not in parent:   # 조문까지 올라왔다(항 번호 없는 조문은 호의 부모가 조문 자신)
            art = store.unit(doc_id, parent.split("/")[0])
            body = AMENDED.sub("", TITLE_LINE.sub("", (art["text"] or "").split(chr(10))[0]) if art else "").strip()
            if body:
                out.append(f"{art['label']}: {excerpt(body, LEAD_CHARS)}")
            break
        p = store.unit(doc_id, parent)
        if not p:
            break
        out.append(f"{p['label']}: {excerpt(AMENDED.sub('', (p['text'] or '').split(chr(10))[0]).strip(), LEAD_CHARS)}")
        cur = p
    return out[::-1]
