"""용어 정의 조회 — 법률 제2조 정의 색인."""
from law_mcp.tools.definition import run_law_definition


def test_definition_of_term(store):
    r = run_law_definition(store, "용도지역")
    assert r["status"] == "ok"
    top = r["facts"]["정의"][0]
    assert top["law"] == "국토의 계획 및 이용에 관한 법률" and top["article"].startswith("제2조")
    assert "용도지역" in top["text"]
    assert top["호출인자"] == {"law": top["law"], "article": top["article"]}


def test_definition_ignores_spaces_and_filters_by_law(store):
    r = run_law_definition(store, "용도 지구", law="국토계획법")
    assert r["status"] == "ok" and all(d["law"] == "국토의 계획 및 이용에 관한 법률" for d in r["facts"]["정의"])


def test_unknown_term_suggests_close_terms(store):
    r = run_law_definition(store, "용도지옄")
    assert r["status"] == "failed" and "용도지역" in r["facts"]["비슷한_용어"]


def test_abbreviation_definition_outside_article_2(store):
    # "개발행위"는 제2조가 아니라 제56조제1항의 "(이하 "개발행위"라 한다)"로 정의된다
    r = run_law_definition(store, "개발행위")
    top = r["facts"]["정의"][0]
    assert (top["law"], top["article"]) == ("국토의 계획 및 이용에 관한 법률", "제56조제1항")


def test_compound_term_falls_back_to_defined_part(store):
    # "용도지역변경"은 정의된 용어가 아니지만 그 안의 "용도지역"은 정의돼 있다
    r = run_law_definition(store, "용도지역변경")
    assert r["status"] == "ok"
    assert r["facts"]["정의"][0]["용어"] == "용도지역"
    assert "용도지역" in r["message"]


def test_definition_with_hanja_after_the_term_is_read():
    """도로교통법 제2조: '"보도"(步道)란 …' — 따옴표 뒤 한자 괄호 때문에 보도·차도 정의를 놓치고 방송법 '보도'만 나왔다."""
    from law_mcp.engine.permits import DEF
    m = DEF.search('10. "보도"(步道)란 연석선, 안전표지나 그와 비슷한 인공구조물로 경계를 표시하여 보행자가 통행할 수 있도록 한 도로의 부분을 말한다.')
    assert m and m.group(1) == "보도"
    assert DEF.search('4. "차도"(車道)란 연석선(차도와 보도를 구분하는 돌 등으로 이어진 선을 말한다. 이하 같다)').group(1) == "차도"
