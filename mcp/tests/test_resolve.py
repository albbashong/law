import pytest

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


def test_article_with_its_own_law_name_in_front_is_read():
    """실측 2026-11-17 qwen3:8b 가 article 에 법령명까지 넣어("도로교통법 제148조의2") 벌칙 조문을 끝내 열지 못했다 —
    앞에 붙은 것이 그 law 의 이름일 때만 떼어 읽는다. 다른 법령 이름(시행규칙 등)이 붙었으면 읽지 않는다."""
    from law_mcp.engine.resolve import strip_law
    assert parse_article(strip_law("도로교통법 제148조의2", "도로교통법")) == "제148조의2"
    assert parse_article(strip_law("「도로교통법」 제44조제1항", "도로교통법")) == "제44조/1"
    assert parse_article(strip_law("도로교통법 시행규칙 별표 28", "도로교통법")) is None
    assert strip_law("제71조", "도로교통법") == "제71조"


class Regions:
    """region_plan 표만 가진 저장소 — (지역, 시도, 조례)"""
    ROWS = [("영천시", "경상북도", 1, "영천시 도시계획 조례"), ("경상북도 영천시", "경상북도", 1, "영천시 도시계획 조례"),
            ("광주", "경기도", 2, "광주시 도시계획 조례"), ("광주시", "경기도", 2, "광주시 도시계획 조례"),
            ("북구", "전남광주통합특별시", 3, "광주광역시 도시계획 조례"),
            ("북구", "부산광역시", 4, "부산광역시 도시계획 조례"),
            ("(구)광주광역시", "(구)광주광역시", 3, "광주광역시 도시계획 조례"),
            ("무안군", "전남광주통합특별시", 5, "무안군 관리계획 조례"),
            ("제주특별자치도", "제주특별자치도", 6, "제주특별자치도 도시계획 조례"),
            ("세종특별자치시", "세종특별자치시", 7, "세종특별자치시 도시계획 조례"),
            ("충주시", "충청북도", 8, "충주시 도시계획 조례")]

    def region_plan(self, region):
        return [{"region": r, "sido": s, "doc_id": d, "name": n, "org": s} for r, s, d, n in self.ROWS if r == region]

    def sidos(self):
        return sorted({s for _, s, _, _ in self.ROWS})


def _names(q):
    return [r["name"] for r in find_region(Regions(), q)]


def test_sido_short_forms_are_matched_against_the_sido_names_in_the_data():
    # 손으로 만든 줄임말 표 없이: 줄임말의 글자가 시도 이름에 순서대로 들어 있다(경북⊂경상북도, 충북⊂충청북도)
    assert _names("경북 영천시") == ["영천시 도시계획 조례"]
    assert _names("충북 충주시") == ["충주시 도시계획 조례"]
    assert _names("전남 무안군") == ["무안군 관리계획 조례"]      # 개편된 시도 이름(전남광주통합특별시)에도 맞는다
    assert _names("세종시") == ["세종특별자치시 도시계획 조례"]


def test_renamed_sido_and_its_old_name_reach_the_same_plan():
    for q in ["광주 북구", "광주광역시 북구", "전남광주통합특별시 북구"]:
        assert _names(q) == ["광주광역시 도시계획 조례"], q


def test_a_city_named_like_a_sido_stays_a_city():
    assert _names("광주시") == ["광주시 도시계획 조례"]
    assert _names("경기 광주시") == ["광주시 도시계획 조례"]


def test_district_with_no_entry_uses_the_plan_of_a_sido_that_is_a_region_itself():
    # 지자체 조례가 없는 행정시 — 시도가 지역으로 올라 있을 때만(도는 올라 있지 않다)
    assert _names("제주특별자치도 제주시") == ["제주특별자치도 도시계획 조례"]
    assert _names("경상북도 포항시") == []
    assert _names("광주 오포읍") == []          # 시·군·구가 아닌 말은 시도 조례로 넘기지 않는다


# ── 이름이 같은 조례(다른 지자체) — 먼저 나온 것을 조용히 고르지 않는다 ─────────────────────────
@pytest.fixture(scope="module")
def twins(tmp_path_factory):
    from indexer.build import build
    from law_mcp.store.db import Store
    out = tmp_path_factory.mktemp("twins") / "lawdb.sqlite"
    build(out, docs=[("ordin", "1973561"), ("ordin", "1920959")], log=lambda m: None)   # 고성군 건축 조례 — 경남·강원
    return Store(out)


def test_same_named_ordinances_of_two_regions_are_asked_about(twins):
    from law_mcp.tools.article import run_law_article
    r = run_law_article(twins, "고성군 건축 조례", "제1조", chain="none")
    assert r["status"] == "needs_disambiguation"
    assert sorted(c["label"] for c in r["facts"]["candidates"]) == [
        "고성군 건축 조례 (강원특별자치도 고성군)", "고성군 건축 조례 (경상남도 고성군)"]


def test_candidate_label_with_the_region_opens_that_ordinance(twins):
    from law_mcp.tools.article import run_law_article
    r = run_law_article(twins, "고성군 건축 조례 (강원특별자치도 고성군)", "제1조", chain="none")
    assert r["status"] == "ok" and r["facts"]["지자체"] == "강원특별자치도 고성군"
