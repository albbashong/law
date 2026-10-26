from law_mcp.engine.resolve import find_doc, find_region, parse_article


def test_find_doc_aliases(store):
    for q in ["국토계획법 시행령", "국토의계획및이용에관한법률시행령", "국토의 계획 및 이용에 관한 법률 시행령"]:
        assert find_doc(store, q)[0]["name"] == "국토의 계획 및 이용에 관한 법률 시행령"


def test_find_doc_unknown_returns_empty(store):
    assert find_doc(store, "없는법령이름") == []


def test_parse_article():
    assert parse_article("제84조제1항제16호") == "제84조/1/16"
    assert parse_article("제84조 제1항 제16호") == "제84조/1/16"
    assert parse_article("별표 17 제2호 차목") == "별표17:2/차"
    assert parse_article("별표17") == "별표17"
    assert parse_article("제56조제16호") == "제56조/-/16"
    assert parse_article("제4조의2") == "제4조의2"
    assert parse_article("84조") == "제84조"


def test_find_region_variants(store):
    for q in ["영천", "영천시", "경상북도 영천시", "경북 영천시"]:
        assert find_region(store, q)[0]["name"] == "영천시 도시계획 조례", q


def test_find_region_ambiguous(store):
    assert len(find_region(store, "중구")) >= 2


def test_general_district_city(store):
    # 일반구가 있는 시: 브이월드 level2 가 "수원시 장안구" 형태로 온다
    for q in ["수원시 장안구", "경기도 수원시 장안구"]:
        rows = find_region(store, q)
        assert rows and rows[0]["name"] == "수원시 도시계획 조례", q


def test_given_sido_never_falls_back_to_other_sido(store):
    rows = find_region(store, "경상북도 포항시 북구")   # 픽스처에 포항 조례 없음
    assert all(r["sido"] == "경상북도" for r in rows)


def test_a_sido_name_no_longer_in_the_data_is_ignored(store):
    # 통합·개편으로 사라진 시도명("전라남도" → 통합특별시)은 시군구로만 찾는다 — 있는 시도명이면 그대로 지킨다
    sidos = {r["sido"] for r in store._all("SELECT DISTINCT sido FROM region_plan")}
    assert "없어진도" not in sidos and "경상북도" in sidos
    assert find_region(store, "없어진도 영천시")[0]["name"] == "영천시 도시계획 조례"
