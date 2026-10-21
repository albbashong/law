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
