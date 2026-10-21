from indexer.citations import citation_path, find_citations


def test_relative_enforcement():
    cs = find_citations("영 제84조제1항의 규정에 의하여 용도지역 안에서의 건폐율은")
    assert len(cs) == 1 and cs[0].rel == "영"
    assert citation_path(cs[0]) == "제84조/1"


def test_named_law_annex():
    cs = find_citations("「건축법 시행령」 별표 1 제17호의 공장 중")
    assert cs[0].law == "건축법 시행령" and citation_path(cs[0]) == "별표1:17"


def test_multiple_and_branch():
    cs = find_citations("법 제75조의3제2항 및 영 제70조의14제2항의 규정에 따라")
    assert [(c.rel, citation_path(c)) for c in cs] == [("법", "제75조의3/2"), ("영", "제70조의14/2")]


def test_same_law_reference_inherits():
    cs = find_citations("「대기환경보전법」 제2조제9호에 따른 물질이 같은 법 시행령 제11조제1항제1호에 따른")
    assert cs[0].law == "대기환경보전법" and citation_path(cs[0]) == "제2조/-/9"
    assert cs[1].law == "대기환경보전법 시행령" and citation_path(cs[1]) == "제11조/1/1"


def test_internal_reference_optional():
    assert find_citations("제33조에 따른 건축물") == []
    cs = find_citations("제33조에 따른 건축물", keep_internal=True)
    assert cs[0].rel == "self" and citation_path(cs[0]) == "제33조"


def test_annex_mok_item():
    cs = find_citations("「국토의 계획 및 이용에 관한 법률 시행령」 별표 17 제2호 차목")
    assert citation_path(cs[0]) == "별표17:2/차"


def test_named_law_followed_by_unnamed_article_in_same_sentence():
    # "「건축법」 제11조 및 제14조" — 두 번째도 같은 법령
    cs = find_citations("「건축법」 제11조 및 제14조에 따라")
    assert [(c.law, citation_path(c)) for c in cs] == [("건축법", "제11조"), ("건축법", "제14조")]


def test_this_rule_is_internal():
    # "이 규칙 제3조"는 자기 문서 참조다
    assert find_citations("이 규칙 제3조에 따른") == []
    assert find_citations("이 조례 제3조에 따른", keep_internal=True)[0].rel == "self"
