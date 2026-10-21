"""용도 분류(건축법 시행령 별표 1)와 관련 인허가를 법령 데이터에서 도출하는지."""
from law_mcp.engine.permits import development_permit, land_permits, permits_for_term
from law_mcp.engine.uses import catalog, match_use


def test_catalog_comes_from_annex_1(store):
    cat = catalog(store)
    names = {c["ho"]: c["name"] for c in cat}
    assert names["1"] == "단독주택" and names["17"] == "공장" and names["18"] == "창고시설"
    assert len(cat) >= 28


def test_match_use_by_annex_text(store):
    assert match_use(store, "공장")[0]["ho"] == "17"
    assert match_use(store, "단독주택")[0]["ho"] == "1"
    assert match_use(store, "창고")[0]["ho"] == "18"
    assert match_use(store, "제2종 근린생활시설")[0]["ho"] == "4"
    assert match_use(store, "별표 1 제17호")[0]["ho"] == "17"


def test_match_use_finds_facility_named_inside_item(store):
    hit = match_use(store, "일반음식점")[0]
    assert hit["ho"] == "4" and hit["label"].startswith("제4호")


def test_match_use_does_not_guess_colloquial_or_single_char(store):
    # 별표 1 원문에 없는 표현은 사전으로 추측하지 않는다 — 호출 측(LLM)이 용도명으로 바꿔 다시 부른다
    assert match_use(store, "카페") == []
    assert match_use(store, "절") == []
    assert match_use(store, "우주정거장") == []


def test_land_permits_from_definition_articles(store):
    r = land_permits(store, "전")
    assert [(x.law, x.article) for x in r] == [("농지법", "제34조")]
    r = land_permits(store, "임야")
    assert [(x.law, x.article) for x in r] == [("산지관리법", "제14조")]
    assert land_permits(store, "대") == []        # 산지 정의의 단서("지목이 대로 변경된 토지 … 제외")에 속지 않는다
    assert land_permits(store, None) == []


def test_permits_for_use_term(store):
    r = permits_for_term(store, ["공장"])
    # 그 말을 정의하는 법률이 여럿이면 제목에 그 말을 가장 많이 쓰는 법률 하나만(특례법 잡음 제외)
    assert [(x.law, x.article) for x in r] == [("산업집적활성화 및 공장설립에 관한 법률", "제13조")]
    assert permits_for_term(store, ["우주정거장"]) == []


def test_development_permit_found_by_title(store):
    r = development_permit(store)
    assert (r.law, r.article) == ("국토의 계획 및 이용에 관한 법률", "제56조")
