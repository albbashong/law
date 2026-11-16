"""열 순서 계산 — 조문 하나에서 인용·위임 연결을 따라가 '열어 볼 조문' 단계를 만든다(검색 계획용).

'꼭 열기'는 연결 자체에서 정한다: follow 가 매긴 관계가 must_relations(위임·상하위·조례 관계)에 들거나,
시작 조문이 바로(한 걸음) 다른 문서를 인용한 것(건축법 제55조 → 국토계획법 제77조처럼 기준을 넘기는 인용).
꼭 열 조문에서 이어진 사슬만, 시작에서 must_depth 걸음 안까지.
같은 문서 안의 인용(정의 조문 등)과 두 걸음 이상의 단순 인용은 참고로만 둔다.
"""
import re

from law_mcp.engine.follow import DELEGATES, follow
from law_mcp.engine.permits import DEF, _clause


LOWER = {"하위", "조례"}     # follow 가 매기는 '위임 받는 쪽' 관계


def _text(store, ref) -> str:
    """조문 글 — 조 단위면 딸린 항·호까지(위임 문구는 항에 있는 일이 많다)."""
    if store is None or ref.path.startswith("별표"):
        return ref.excerpt or ""
    return "\n".join(u["text"] or "" for u in store.units_with_prefix(ref.doc_id, ref.path)) or ref.excerpt or ""


def _defines(store, ref) -> bool:
    """정의 조문인가 — 그 단위나 위 단위(호의 목이면 호)의 문장이 '"○○"이란 … 말한다' 꼴(법률 용어)."""
    if store is None or ref.path.startswith("별표"):
        return bool(DEF.search(_clause(ref.excerpt)))
    segs = ref.path.split("/")
    for i in range(len(segs), 0, -1):
        u = store.unit(ref.doc_id, "/".join(segs[:i]))
        if u and DEF.search(_clause((u["text"] or "").split("\n", 1)[0])):
            return True
    return False


ARTICLE = re.compile(r"^(제\d+조(?:의\d+)?|별표\s*\d+(?:의\d+)?)")   # 조·별표 번호(항·호 앞까지)


def _unit_key(ref) -> tuple:
    """같은 조(별표)면 같은 열쇠 — 항·호는 조를 열면 함께 보인다."""
    return ref.doc_id, (ref.path.split(":")[0] if ref.path.startswith("별표") else ref.path.split("/")[0])


def route(store, doc_id: int, path: str, region_doc_id: int | None, depth: int, steps_max: int,
          must_relations: set[str], neighbors: int = 12, must_depth: int = 2) -> dict:
    r = follow(store, doc_id, path, region_doc_id, "both", depth, neighbors)
    index: dict[tuple, int] = {}
    steps: list[dict] = []
    delegates: dict[int, bool] = {}     # 단계 번호 → 그 조문에 위임 문구가 있나
    units: dict[tuple, int] = {}       # (문서, 조) → 단계 번호
    for ref in r.refs:
        back = index.get(ref.parent) if ref.parent else None
        if ref.parent and back is None:
            continue        # 이끈 조문이 상한에 잘렸다 — 이어지지 않는 단계는 싣지 않는다
        # 꼭 열기는 시작에서 꼭 열 조문을 따라 이어진 사슬만, must_depth 걸음 안에서 — 참고 인용 아래로 내려간 상하위나
        # 멀리 간 연결까지 꼭 열기로 치면 작은 질문에도 열 조문이 열 개를 넘는다(실측 2026-11-16 건폐율 질문 11개)
        chained = back is None or steps[back]["꼭_열기"]
        # 하위·조례는 이끈 조문이 실제로 위임할 때만 — 위임 없는 조문을 거꾸로 인용하는 하위 법령(적용 완화 등)은 참고
        delegated = ref.relation not in LOWER or (back is not None and delegates[back])
        # 다른 법령으로 넘기는 인용은 꼭 열기 — 단 그 법령의 정의 조문(용어 뜻)은 참고(spec §5)
        crossing = ref.depth == 1 and ref.doc_id != doc_id and not _defines(store, ref)
        must = chained and delegated and ref.depth <= must_depth and (ref.relation in must_relations or crossing)
        gives = bool(DELEGATES.search(_text(store, ref)))
        same = units.get(_unit_key(ref))
        if same is not None:        # 같은 조의 다른 항·호 — 한 단계로 합친다
            steps[same]["꼭_열기"] = steps[same]["꼭_열기"] or bool(must)
            delegates[same] = delegates[same] or gives
            index[(ref.doc_id, ref.path)] = same
            continue
        if len(steps) >= steps_max:
            break
        m = ARTICLE.match(ref.article or "")
        delegates[len(steps)] = gives
        index[(ref.doc_id, ref.path)] = units[_unit_key(ref)] = len(steps)
        steps.append({"law": ref.law, "article": m.group(1) if m else ref.article, "관계": ref.relation,
                      "꼭_열기": bool(must), "이어진": back})
    return {"단계": steps, "멈춤": list(r.more)}
