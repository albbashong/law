"""검색어 낱말을 법령이 쓰는 꼴로 잇기, 지역 안에서 못 찾으면 지역을 빼고 찾기.

실측 2026-11-09 '서대문구 노후건축물 기준': 법령 용어는 '노후ㆍ불량건축물'인데 '노후건축물'로 찾아 정비법 시행령
제2조·서울 정비 조례 제4조를 못 찾았고, region 을 주자 낱말이 하나도 안 맞는 서울시 조례(가로수·병원)가 결과로 나왔다."""
from law_mcp.tools.search import _term_variants, run_law_search


def _refs(r):
    return [(x["law"], x["article"]) for x in r["facts"]["결과"]]


def test_a_word_missing_the_dotted_part_finds_the_law_term(store):
    # '도시관리계획' 은 법령에서 '도시ㆍ군관리계획' — 제목에 가운뎃점으로 낀 말이 빠진 꼴
    assert _term_variants(store, "도시관리계획 결정") == {"도시관리계획": ["도시ㆍ군관리계획"]}


def test_a_word_the_titles_already_use_has_no_variant(store):
    assert _term_variants(store, "용도지역 건축제한") == {}


def test_search_includes_articles_found_by_the_law_term(store):
    r = run_law_search(store, "도시관리계획 결정", scope="law")
    assert ("국토의 계획 및 이용에 관한 법률", "제30조") in _refs(r)[:4]
    assert "도시ㆍ군관리계획" in r["message"]


def test_the_region_name_in_the_query_does_not_count_as_a_match(store):
    # 질의의 지역 이름은 그 지역 조례 어디에나 나온다 — 그것만 맞았으면 맞은 것이 아니다
    r = run_law_search(store, "영천시 산지전용허가 기준", region="영천시")
    assert any(law == "산지관리법" for law, _ in _refs(r))


def test_region_search_without_any_matching_word_searches_without_the_region(store):
    r = run_law_search(store, "산지전용허가 기준", region="영천시")
    assert any(law == "산지관리법" for law, _ in _refs(r))
    assert "영천시" in r["message"]


def test_law_term_results_lead_when_the_plain_word_matches_nothing(store):
    # 줄인 꼴이 든 조문이 없으면 그 검색 결과는 다른 낱말만 맞은 것 — 법령 용어로 찾은 것을 앞에 둔다
    r = run_law_search(store, "도시관리계획 기준", scope="law")
    assert "도시ㆍ군관리계획" in r["facts"]["결과"][0]["title"]


def test_query_words_keep_nouns_whole_and_drop_only_particles():
    # 조사 목록으로 끝을 떼면 명사가 잘렸다(건축허가 → 건축허, 진입로 → 진입, 어린이 → 어린) — 형태소 분석으로 뗀다
    from law_mcp.tools.search import _query_words
    assert _query_words("건축허가 대상") == ["건축허가", "대상"]
    assert _query_words("건축허가를 받으려면")[0] == "건축허가"
    assert _query_words("용도지역에서의 건폐율은") == ["용도지역", "건폐율"]
    assert _query_words("고속도로 진입로 어린이 환경영향평가") == ["고속도로", "진입로", "어린이", "환경영향평가"]
