"""딸린 문장 — 호·목은 그것이 딸린 항의 문장(적용 대상·요건)과 함께 읽어야 한다.

호 한 줄("0.03퍼센트 이상 … 1년 이상 5년 이하")만 보고, 그 호가 딸린 항의 요건("10년 내에 다시 위반한 사람")을
놓쳐 초범에 재범 조항을 적용하던 문제(실측 2026-10-27, 도로교통법 제148조의2). 구조(상위 경로)로만 찾는다.
"""
from law_mcp.engine.structure import leads
from law_mcp.tools.article import run_law_article
from law_mcp.tools.search import run_law_search

LAW = "국토의 계획 및 이용에 관한 법률 시행령"


def _doc(store):
    return store._one("SELECT doc_id FROM doc WHERE name=?", (LAW,))["doc_id"]


def test_item_comes_with_the_paragraph_it_hangs_from(store):
    got = leads(store, _doc(store), "제84조/1/3")
    assert len(got) == 1 and got[0].startswith("제84조제1항: ①법 제77조제1항 및 제2항에 따른 건폐율은")


def test_paragraph_itself_has_no_lead(store):
    assert leads(store, _doc(store), "제84조/1") == []


def test_focus_word_finds_the_item_inside_a_whole_article(store):
    # 조 전체가 걸린 검색 결과 — 발췌한 낱말이 든 가장 깊은 호를 찾아 그 위 항의 문장을 붙인다
    got = leads(store, _doc(store), "제84조", focus="제1종일반주거지역")
    assert got and got[0].startswith("제84조제1항:")


def test_article_tool_shows_the_lead_of_an_opened_item(store):
    f = run_law_article(store, LAW, "제84조제1항제3호", chain="none")["facts"]
    assert f["딸린_문장"][0].startswith("제84조제1항:")


def test_search_rows_carry_the_lead_of_the_matched_item(store):
    rows = run_law_search(store, "제1종일반주거지역 건폐율", scope="law")["facts"]["결과"]
    hit = next(r for r in rows if r["law"] == LAW and r["article"].startswith("제84조"))
    assert any(x.startswith("제84조제1항:") for x in hit.get("딸린_문장", []))
