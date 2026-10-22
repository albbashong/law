"""조문·별표 조회 + 상위/하위 근거 체인."""
from typing import Annotated, Literal

from pydantic import Field

from law_mcp.config import get_settings
from law_mcp.engine.facts import excerpt, fit, pack, size
from law_mcp.engine.structure import structure
from law_mcp.engine.follow import deemed_items, follow, node
from law_mcp.engine.resolve import find_doc, find_region, name_key, parse_article, suggest
from law_mcp.tools.common import READ_ONLY, candidates_result, result, trace

ORIGINAL_MAX = 2500


def resolve_doc(store, law: str):
    """(doc 또는 None, 실패/되묻기 결과 또는 None)"""
    docs = find_doc(store, law)
    if not docs:
        return None, result("failed", {"제안": suggest(store, law)},
                            f"'{law}' 법령·조례를 찾지 못했습니다. 정식 명칭이나 제안 목록의 이름으로 다시 호출하세요.")
    exact = [d for d in docs if d["name_key"] == name_key(law) or len(docs) == 1]
    if exact:
        return exact[0], None
    names = {d["name"] for d in docs}
    if len(names) == 1:
        return docs[0], None
    return None, candidates_result("법령", docs, "name", f"'{law}'에 해당하는 법령이 여럿입니다. 하나를 골라 주세요.")


def resolve_region(store, region: str | None):
    if not region:
        return None, None
    rows = find_region(store, region)
    if not rows:
        return None, result("failed", {}, f"'{region}'의 도시·군계획 조례를 찾지 못했습니다. 예: '영천시', '경상북도 영천시'")
    if len(rows) > 1:
        return None, candidates_result("지역", [{"doc_id": r["doc_id"], "org": r["org"]} for r in rows], "org",
                                       f"'{region}'은 여러 지자체에 있습니다. 시도를 함께 알려 주세요.")
    return rows[0], None


def run_law_article(store, law: str, article: str, region: str | None = None,
                    chain: str = "up", depth: int | None = None) -> dict:
    s = get_settings()
    depth = s.follow_depth if depth is None else depth
    doc, err = resolve_doc(store, law)
    if err:
        return err
    reg, err = resolve_region(store, region)
    if err:
        return err
    path = parse_article(article)
    if not path:
        return result("failed", {}, f"'{article}'을 조문·별표 번호로 읽지 못했습니다. 예: '제71조', '제84조제1항제16호', '별표 17 제2호 차목'")
    start = node(store, doc["doc_id"], path)
    if not start:
        hint = {}
        if path.startswith("별표"):
            hint["별표목록"] = [f"별표 {a['no']} {a['title'] or ''}"[:60] for a in store.annexes(doc["doc_id"])][:30]
        return result("failed", hint, f"{doc['name']}에 {article}이(가) 없습니다.")
    r = follow(store, doc["doc_id"], path, reg["doc_id"] if reg else None, chain, depth)
    body = start.excerpt
    if not path.startswith("별표") and "/" in path:   # 항·호는 자기 줄만 저장돼 있다 — 하위 호·목까지 잇는다
        body = "\n".join(u["text"] or "" for u in store.units_with_prefix(doc["doc_id"], path)) or body
    facts = {"법령": doc["name"], "조문": start.article, "제목": start.title,
             "원문": excerpt(body, ORIGINAL_MAX),
             "호출인자": {"law": doc["name"], "article": start.article}}   # 검색 결과와 같은 모양 — 이 조문 자신
    names = store.aliases(doc["doc_id"])
    if names:   # 답에 약칭을 써도 조회 결과로 확인된다
        facts["약칭"] = names
    if path.startswith("별표") and ":" not in path:
        a = store.annex(doc["doc_id"], path[2:])
        items = store.annex_items(a["annex_id"])
        facts["항목"] = [i["label"] + " " + excerpt(i["text"], 40) for i in items if "/" not in i["path"]][:30]
    if not path.startswith("별표"):
        parts = structure(store, doc["doc_id"], path)
        if parts:   # 나열된 호·목의 관계(별개 사유·모두 충족·구분) — 원문 연결 문구에서
            facts["구조"] = parts
        deemed = deemed_items(store, doc["doc_id"], path)
        if deemed:   # 이 조항의 허가 등을 받으면 받은 것으로 보는 것들 — 이웃 수 제한 없이 짧게 전부
            facts["의제_목록"] = deemed[:40]
    budget = s.facts_max_chars - size(facts) - 400
    refs, over = pack(r.refs[1:], budget, s.excerpt_chars)
    facts["refs"] = refs
    facts["더_볼_연결"] = (r.more + over)[:10]
    fit(facts, s.facts_max_chars)
    msg = (f"{doc['name']} {start.article} 원문과 연결된 근거 {len(refs)}건입니다."
           " 더_볼_연결이 남아 있으면 law_article로 이어서 조회하세요.")
    return result("partial" if not refs and r.more else "ok", facts, msg)


def register(mcp, get_store):
    @mcp.tool(title="조문·별표 조회 — 근거 법령 원문과 상위법·조례 위임 체인", annotations=READ_ONLY)
    def law_article(
        law: Annotated[str, Field(description="법령·조례 이름. 약칭 가능 (예: 국토계획법 시행령, 영천시 도시계획 조례)")],
        article: Annotated[str, Field(description="조문·별표 번호 (예: 제71조, 제84조제1항제16호, 별표 17 제2호 차목)")],
        region: Annotated[str | None, Field(description="조례 위임을 따라갈 시군구 (예: 영천시)")] = None,
        chain: Annotated[Literal["up", "down", "both", "none"], Field(description="up=상위 근거, down=위임받은 하위·조례")] = "up",
        depth: Annotated[int, Field(ge=0, le=4)] = 2,
    ) -> dict:
        """근거 법령·조문 원문, 조례 위임, 상위법을 찾는다 — "건축법 제11조 근거", "시행령 별표 17", "조례로 정한 내용".

        특정 조문·별표의 원문을 돌려주고, 위임("대통령령으로", "조례로 정한다")과 인용("영 제84조")을
        따라가 상위·하위 근거를 refs 로 함께 준다. region 을 주면 그 시군구 조례까지 내려간다.
        facts.더_볼_연결 은 이 도구에 그대로 다시 넣을 수 있는 인자다.
        허가 여부는 판정하지 않는다.
        """
        return trace("law_article", dict(law=law, article=article, region=region, chain=chain, depth=depth), run_law_article(get_store(), law, article, region, chain, depth))
