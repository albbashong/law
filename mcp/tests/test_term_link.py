"""말 연계 — 법제처 일상용어–법령용어 연계와 법령명 검색으로, 사용자가 쓴 일상어·별칭을 법령 용어·법령명에 잇는다."""
import json
from pathlib import Path

import httpx

from law_mcp.clients import lawgo
from law_mcp.tools.term_link import run_term_link

FIX = Path(__file__).parent / "fixtures"
WORDS = {"그린벨트": "greenbelt", "김영란법": "kim", "축대": "chukdae"}


def _transport(calls=None):
    def handler(req: httpx.Request):
        p = req.url.params
        if calls is not None:
            calls.append(dict(p))
        if req.url.path.endswith("lawService.do"):
            name = f"lawgo_dlytrmrlt_{p['MST']}.json"
        else:
            name = f"lawgo_{p['target']}_{WORDS[p['query']]}.json"
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))
    return httpx.MockTransport(handler)


def client(calls=None):
    return lawgo.Client("test-oc", transport=_transport(calls), retry_wait=0)


def test_daily_word_links_to_legal_term_with_relation():
    links = client().daily_term_links("그린벨트")
    assert links == [{"법령용어": "개발제한구역", "관계": "동의어"}, {"법령용어": "녹지", "관계": "연관어"}]


def test_only_the_exact_daily_word_is_followed():
    # "축대" 검색은 '가축대소변처리' 같은 다른 말만 돌려준다 — 같은 말이 없으면 연계도 없다
    assert client().daily_term_links("축대") == []


def test_law_name_search_returns_names_and_short_names():
    assert client().law_names("김영란법")[:1] == [{"법령": "부정청탁 및 금품등 수수의 금지에 관한 법률", "약칭": "청탁금지법"}]
    assert client().law_names("축대") == []


def test_answers_are_cached_per_word():
    calls = []
    c = client(calls)
    c.daily_term_links("그린벨트")
    c.daily_term_links("그린벨트")
    assert len(calls) == 2      # 검색 1 + 연계 1, 두 번째는 저장된 것


def test_term_link_tool_separates_synonyms_from_related_terms():
    out = run_term_link(None, client(), "그린벨트")
    f = out["facts"]
    assert out["status"] == "ok"
    assert f["같은_뜻_법령용어"] == ["개발제한구역"] and f["관련_법령용어"] == ["녹지"]
    assert f["법령명_검색"][0]["법령"] == "개발제한구역의 지정 및 관리에 관한 특별조치법"


def test_term_link_tool_says_so_when_nothing_links(store):
    out = run_term_link(store, client(), "축대")
    assert out["status"] == "failed"
    assert "기억만으로" in out["message"] and "law_search" in out["message"]   # 짐작 대신 원문 확인


def test_term_link_tool_marks_related_only_words():
    # 김영란법 → 연관어 '공직자'뿐: 같은 뜻 용어로 쓰면 안 된다. 법령명 검색이 청탁금지법을 준다
    f = run_term_link(None, client(), "김영란법")["facts"]
    assert "같은_뜻_법령용어" not in f and f["관련_법령용어"] == ["공직자"]
    assert f["법령명_검색"][0]["약칭"] == "청탁금지법"


def test_laws_missing_from_the_local_index_are_not_offered(store):
    # 조회할 수 없는 법령명은 건네지 않는다 — 시험 색인에는 개발제한구역법이 없다
    f = run_term_link(store, client(), "그린벨트")["facts"]
    assert "법령명_검색" not in f and f["같은_뜻_법령용어"] == ["개발제한구역"]


def test_no_key_means_the_tool_reports_failure():
    assert run_term_link(None, None, "그린벨트")["status"] == "failed"
