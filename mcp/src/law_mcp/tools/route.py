"""law_route — 조문 하나에서 열어 볼 순서(검색 계획의 경로)."""
from typing import Annotated

from pydantic import Field

from law_mcp.config import get_settings
from law_mcp.engine.resolve import parse_article
from law_mcp.engine.route import route
from law_mcp.tools.article import resolve_doc, resolve_region
from law_mcp.tools.common import READ_ONLY, result, trace


def run_law_route(store, law: str, article: str, region: str | None = None) -> dict:
    s = get_settings()
    doc, err = resolve_doc(store, law)
    if err:
        return err
    reg, err = resolve_region(store, region)
    if err:
        return err
    path = parse_article(article)
    if not path:
        return result("failed", {}, f"'{article}'을 조문·별표 번호로 읽지 못했습니다. 예: '제71조', '별표 17'")
    r = route(store, doc["doc_id"], path, reg["doc_id"] if reg else None, s.route_depth, s.route_steps_max,
              {x.strip() for x in s.route_must_relations.split(",") if x.strip()}, s.route_neighbors, s.route_must_depth)
    if not r["단계"]:
        return result("failed", {}, f"{doc['name']}에 {article}이(가) 없습니다.")
    must = sum(x["꼭_열기"] for x in r["단계"])
    return result("ok", r, f"{doc['name']} {r['단계'][0]['article']}에서 열어 볼 조문 {len(r['단계'])}개(꼭 열 조문 {must}개)입니다.")


def register(mcp, get_store):
    @mcp.tool(title="열어 볼 순서 — 조문 하나에서 위임·인용 연결을 따라갈 경로", annotations=READ_ONLY)
    def law_route(
        law: Annotated[str, Field(description="법령·조례 이름 (예: 건축법, 국토계획법 시행령)")],
        article: Annotated[str, Field(description="시작 조문·별표 (예: 제55조, 별표 1)")],
        region: Annotated[str | None, Field(description="조례까지 내려갈 시군구 (예: 영천시)")] = None,
    ) -> dict:
        """이 조문에서 무엇을 어떤 순서로 열어야 하나 — 위임·상하위·조례·별표 연결을 따라 열어 볼 조문 단계를 준다.

        단계마다 law_article 에 그대로 넣을 법령·조문, 관계, 꼭_열기(위임·상하위·조례 관계와 다른 법령으로 넘기는 인용)를 싣는다.
        원문은 싣지 않는다 — 원문은 law_article 로 연다.
        """
        return trace("law_route", dict(law=law, article=article, region=region),
                     run_law_route(get_store(), law, article, region))
