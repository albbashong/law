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


def test_match_use_by_ho_and_mok_number(store):
    # 번호로 부르면 뒤에 붙은 말이 깨져도 그 호·목으로 본다 — 모델이 목록에서 고른 것을 번호로 넘기게
    hit = match_use(store, "제3호나목 휴게음식점")[0]
    assert hit["ho"] == "3" and hit["label"] == "제3호 나목"
    assert match_use(store, "제4호 자목")[0]["label"] == "제4호 자목"
    assert match_use(store, "제3호 제1부사")[0]["label"].startswith("제3호")


def test_names_are_numbered_by_ho_and_mok(store):
    from law_mcp.engine.uses import names
    first = next(n for n in names(store) if n.startswith("제3호 "))
    assert "나목 휴게음식점" in first


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


def test_use_list_shows_every_facility_the_law_enumerates(store):
    # 목 머리에 열거된 시설을 모두 — "러. 안마시술소, 노래연습장" 의 노래연습장도 고를 수 있어야 한다.
    # 설명형 목("…일용품을 판매하는 소매점으로서 …")은 설명이 가리키는 시설(소매점)으로
    from law_mcp.engine.uses import facility_terms
    assert facility_terms("러. 안마시술소, 노래연습장") == ["안마시술소", "노래연습장"]
    assert facility_terms("차. 장의사, 동물병원, 그 밖에 이와 유사한 것") == ["장의사", "동물병원"]
    assert facility_terms("가. 일반숙박시설 및 생활숙박시설(「공중위생관리법」 제3조제1항 전단에 따라") == ["일반숙박시설", "생활숙박시설"]
    assert facility_terms("가. 식품·잡화·의류·완구·서적·건축자재·의약품·의료기기 등 일용품을 판매하는 소매점으로서 같은 건축물에") == ["소매점"]
    assert facility_terms("다. 자동차영업소로서 같은 건축물에 해당 용도로 쓰는 바닥면적의 합계가 1천 제곱미터 미만인 것") == ["자동차영업소"]
    assert facility_terms("나. 휴게음식점, 제과점 등 음료·차(茶)·음식·빵·떡·과자 등을 조리하거나 제조하여 판매하는 시설") == ["휴게음식점", "제과점"]
    assert facility_terms("머. 「물류시설의 개발 및 운영에 관한 법률」 제2조제5호의2에 따른 주문배송시설로서 같은 건축물에") == ["주문배송시설"]


def test_match_use_by_a_later_enumerated_facility_and_late_mok_letter(store):
    hit = match_use(store, "노래연습장")[0]
    assert hit["ho"] == "4" and hit["label"].endswith("러목") and hit["term"] == "노래연습장"
    assert match_use(store, "제4호러목")[0]["label"].endswith("러목")
