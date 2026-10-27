"""말 연계 — 사용자가 쓴 일상어·별칭·줄임말을 법령 용어와 법령명에 잇는다 (법제처 연계 자료).

같은 뜻(동의어)만 바꿔 쓸 수 있는 용어로 주고, 연관어는 참고로만 나눠 준다. 연계도 법령명 검색도 없으면
기억으로 바꾸지 말라고 알린다 — 모르는 말을 틀리게 확신하던 문제(그린벨트→녹지지역, 김영란법→공직자윤리법).
"""
from typing import Annotated

from pydantic import Field

from law_mcp.clients.lawgo import Client, LawgoUnavailable
from law_mcp.config import get_settings
from law_mcp.store.db import nospace
from law_mcp.tools.common import READ_ONLY, result, trace

SYNONYM = "동의어"
_client: dict = {}


def default_client() -> Client | None:
    s = get_settings()
    if not s.law_api_oc:
        return None
    return _client.setdefault(s.law_api_oc, Client(s.law_api_oc, s.lawgo_timeout))


def run_term_link(store, client: Client | None, word: str) -> dict:
    word = (word or "").strip()
    if len(nospace(word)) < 2:
        return result("failed", {}, "말을 2글자 이상으로 적어 주세요. 예: 그린벨트, 김영란법")
    if client is None:
        return result("failed", {}, "법제처 연계 자료를 쓸 수 없습니다(LAW_API_OC 없음). law_search 로 원문에서 찾으세요.")
    try:
        links, laws = client.daily_term_links(word), client.law_names(word)
    except LawgoUnavailable as e:
        return result("failed", {}, f"법제처 연계 자료 조회에 실패했습니다({e}). law_search 로 원문에서 찾으세요.")
    if store is not None:   # 로컬 색인에 있는 법령만 — 조회할 수 없는 이름을 건네지 않는다
        laws = [x for x in laws if store.docs_by_key(nospace(x["법령"]))]
    facts: dict = {"말": word}
    same = [x["법령용어"] for x in links if x["관계"] == SYNONYM]
    other = [x["법령용어"] for x in links if x["관계"] != SYNONYM]
    if same:
        facts["같은_뜻_법령용어"] = same
    if other:
        facts["관련_법령용어"] = other
    if laws:
        facts["법령명_검색"] = laws
    if len(facts) == 1:
        return result("failed", facts,
                      f"'{word}'은(는) 법제처 일상용어 연계와 법령명 검색에 없습니다. 기억만으로 다른 용어·법령으로 바꾸지 마세요. "
                      "같은 발화의 다른 말로 보아 법령 용어의 줄임말이면 풀어 쓴 용어로 law_search 해 원문에 그 용어가 "
                      "나오는지 확인한 뒤 쓰고, 이 말 그대로 law_search 해 쓰는 조문이 있는지도 봅니다. 원문에서 확인되지 "
                      "않을 때만 사용자에게 무엇을 뜻하는지 물으세요. 법령 이름이라면 현행 법령 목록에 없는 이름(폐지된 법령이나 "
                      "비공식 명칭)일 수 있으니, 비슷한 다른 법령으로 바꾸지 말고 현행 법령에서 확인되지 않는다고 답하세요.")
    msg = (f"법제처 연계 자료입니다. 같은_뜻_법령용어는 '{word}' 대신 검색어로 쓸 수 있습니다. "
           "관련_법령용어는 같은 뜻이 아니니 그대로 바꿔 쓰지 마세요. 법령명_검색은 이 말로 찾은 법령입니다.")
    return result("ok", facts, msg)


def register(mcp, get_store):
    @mcp.tool(title="말 연계 — 일상어·별칭을 법령 용어·법령명으로 (법제처 연계 자료)", annotations=READ_ONLY)
    def term_link(
        word: Annotated[str, Field(description="사용자가 쓴 일상어·별칭·줄임말 하나 (예: 그린벨트, 김영란법, 축대)")],
    ) -> dict:
        """그린벨트·김영란법처럼 법령에 그대로 나오지 않는 말을 법령 용어·법령명에 잇는다.

        법제처 일상용어–법령용어 연계(동의어·연관어)와 법령명 검색(약칭·별칭 포함)을 함께 본다. 같은 뜻 용어만
        검색어로 바꿔 쓰고, 아무 연계도 없으면 짐작하지 말고 원문 검색이나 되묻기로 확인한다.
        """
        return trace("term_link", dict(word=word), run_term_link(get_store(), default_client(), word))
