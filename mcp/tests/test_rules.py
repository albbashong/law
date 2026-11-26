from law_mcp.engine.rules import normalize_zone


def test_normalize_zone():
    assert normalize_zone("자연녹지") == "자연녹지지역"
    assert normalize_zone("자연 녹지 지역") == "자연녹지지역"
    assert normalize_zone("계획관리") == "계획관리지역"
    assert normalize_zone("제2종일반주거") == "제2종일반주거지역"


def test_mentions_distinguishes_branch_numbers():
    from law_mcp.engine.rules import mentions
    assert mentions("「건축법 시행령」 별표 1 제1호의 단독주택", "1", "1")
    assert not mentions("「건축법 시행령」 별표 1 제23호의2의 국방ㆍ군사시설", "23", "1")
    assert mentions("「건축법 시행령」 별표 1 제23호의2의 국방ㆍ군사시설", "23의2", "1")
    assert not mentions("별표 1 제17호의 공장", "1", "1")
    assert mentions("별표 2 제17호의 공장", "17", "2") and not mentions("별표 2 제17호의 공장", "17", "1")   # 분류표 번호는 데이터에서


def test_section_kind_ignores_parenthetical():
    from law_mcp.engine.rules import section_kind
    assert section_kind("1. 건축할 수 있는 건축물(4층 이하. 다만, 도시ㆍ군계획조례로 따로 층수를 정하는") == "허용"
    assert section_kind("2. 도시ㆍ군계획조례가 정하는 바에 의하여 건축할 수 있는 건축물(4층 이하") == "조례허용"
    assert section_kind("1. 건축할 수 없는 건축물") == "불가"


def test_is_partial():
    # 낱말 목록이 아니라 구조로: 호 인용 뒤에 그 용도 이름만 오면 전체, 다른 말이 붙으면 일부
    from law_mcp.engine.rules import is_partial
    assert not is_partial("바. 「건축법 시행령」 별표 1 제14호의 업무시설", "14", "1", "업무시설")
    assert is_partial("자. 「건축법 시행령」 별표 1 제17호의 공장으로서 성장관리계획 및 지구단위 계획이 수립되지 않은 지역에 설치하는 것", "17", "1", "공장")
    assert is_partial("파. 「건축법 시행령」 별표 1 제18호의 창고시설(창고 중 농업·임업·축산 업·수산업용으로 쓰는 것은 제외한다)", "18", "1", "창고시설")
    assert is_partial("가. 「건축법 시행령」 별표 1 제15호의 숙박시설 중 일반숙박시설 및 생활 숙박시설.", "15", "1", "숙박시설")


def test_whole_use_beside_another_use_in_the_same_line():
    # 한 줄에 용도가 둘 — 뒤 용도의 한정("중 기숙사")은 앞 용도에 붙지 않는다(실측: 예전 규칙은 단독주택도 일부로 봤다)
    from law_mcp.engine.rules import is_partial
    line = "가. 「건축법 시행령」 별표 1 제1호의 단독주택, 별표 1 제2호의 공동주택 중 기숙사"
    assert not is_partial(line, "1", "1", "단독주택") and is_partial(line, "2", "1", "공동주택")


def test_a_narrower_name_than_the_use_is_partial():
    # 제28호는 장례시설인데 조례가 "장례식장"만 적었다 — 그 용도 전체가 아니다(예전 규칙은 전체로 봤다)
    from law_mcp.engine.rules import is_partial
    assert is_partial("터. 「건축법 시행령」 별표 1 제28호의 장례식장", "28", "1", "장례시설")
    assert not is_partial("10. 「건축법 시행령」 별표 1 제28호의 장례시설<신설 2008. 12. 31, 개정 2019. 2. 6>", "28", "1", "장례시설")


def test_a_reference_that_narrows_the_same_use_is_partial():
    # 실측 평창군: 이름 없이 곧바로 같은 호의 목 인용이 온다 — 다음 용도가 아니라 이 호를 좁히는 말
    from law_mcp.engine.rules import is_partial
    assert is_partial("라. 「건축법 시행령」 별표 1 제21호의 제21호가목에 따른 시설 중 양어시설(양식장을 포함한다)", "21", "1",
                      "동물 및 식물 관련 시설")
    # "및"으로 묶인 뒤 용도의 괄호 단서는 앞 용도에도 걸릴 수 있다
    both = "(1) 「건축법 시행령」 별표 1 제3호의 제1종 근린생활시설 및 제4호의 제2종 근린생활시설(휴게음식점에 한정한다)"
    assert is_partial(both, "3", "1", "제1종 근린생활시설")
    assert not is_partial("나. 「건축법 시행령」 별표 1 제18호의 창고시설 및 제20호의 자동차 관련 시설", "18", "1", "창고시설")
    assert not is_partial("나. 「건축법 시행령」별표 1 제1호, 제2호의 공동주택", "1", "1", "단독주택")     # 호만 나열


def test_middle_dot_variants_are_the_same_name():
    from law_mcp.engine.rules import is_partial
    for dot in "·․ㆍ":
        assert not is_partial(f"거. 「건축법 시행령」 별표 1 제23호의2의 국방{dot}군사시설", "23의2", "1", "국방ㆍ군사시설")


def test_section_kind_reads_the_statutory_phrase_or_says_unknown():
    from law_mcp.engine.rules import section_kind
    assert section_kind("2. 지역 여건 등을 고려하여 도시ㆍ군계획조례로 정하는 바에 따라 건축할 수 없는 건축물") == "조례제한"
    assert section_kind("가. 「건축법 시행령」 별표 1 제1호의 단독주택") is None     # 머리말이 아니다 — 허용으로 치지 않는다


def test_item_with_enumerated_children_is_partial():
    # 하위 항목으로 대상을 열거하면(“다음의 어느 하나”) 그 용도 전체가 아니다 — 문구가 아니라 구조로 판단
    from law_mcp.engine.rules import is_partial_item
    items = [{"path": "15", "text": "15. 별표 1 제17호의 공장"}, {"path": "15/가", "text": "가. 식품 제조업"},
             {"path": "16", "text": "16. 별표 1 제18호의 창고시설"}]
    assert is_partial_item(items, items[0], "17", "1", "공장")
    assert not is_partial_item(items, items[2], "18", "1", "창고시설")


def test_subtype_cover_by_mok_letters_and_exclusions():
    # 사용자가 목 단위(일반음식점 = 제4호 자목)로 물으면, 항목이 그 목을 명시·제외하는지로 본다
    from law_mcp.engine.rules import subtype_cover
    allow_except = "다. 「건축법 시행령」 별표 1 제4호의 제2종 근린생활시설[같은 호 아목, 자 목, 더목 및 러목(안마시술소만 해당한다)은 제외한다]"
    listed = "나. 「건축법 시행령」 별표 1 제4호아목ㆍ자목 및 러목(안마시술소만 해당 한다)에 따른 제2종 근린생활시설"
    other_excluded = "4. 「건축법 시행령」 별표 1 제4호의 제2종 근린생활시설(단란주점을 제외한다)"
    assert subtype_cover(allow_except, "4", "자", "일반음식점") is False
    assert subtype_cover(listed, "4", "자", "일반음식점") is True
    assert subtype_cover(listed, "4", "가", "공연장") is False
    assert subtype_cover(other_excluded, "4", "자", "일반음식점") is True
    assert subtype_cover(other_excluded, "4", "너", "단란주점") is False
    assert subtype_cover("15. 「건축법 시행령」 별표 1 제17호의 공장 중 다음의 어느 하나", "17", None, None) is None
    # "단독주택 중 다가구주택"처럼 한정 문구가 그 시설명을 직접 부르면 그 목 전체를 포함한다
    assert subtype_cover("가. 「건축법 시행령」 별표 1 제1호의 단독주택 중 다가구주택", "1", "다", "다가구주택") is True
    assert subtype_cover("가. 「건축법 시행령」 별표 1 제1호의 단독주택(다가구주택을 제외한다)", "1", "다", "다가구주택") is False
