"""잘라 낸 것은 '생략'에 적는다 — 법령 원문·규제 목록이 조용히 줄면 답이 달라진다(전수조사 3B)."""
from law_mcp.clients.vworld import Parcel
from law_mcp.tools.article import run_law_article
from law_mcp.tools.common import capped
from law_mcp.tools.parcel import run_parcel_zoning


def test_capped_list_records_what_was_left_out():
    facts = {}
    capped(facts, "조건", ["a", "b", "c"], 2)
    assert facts["조건"] == ["a", "b"] and "3건 중 2건" in facts["생략"]["조건"]


def test_list_within_the_cap_records_nothing():
    facts = {}
    capped(facts, "조건", ["a", "b"], 2)
    assert facts == {"조건": ["a", "b"]}


def test_long_article_says_its_text_was_cut(store):
    r = run_law_article(store, "건축법", "제2조", chain="none")
    assert r["facts"]["원문"].endswith("…") and "원문" in r["facts"]["생략"]


def test_short_article_has_nothing_omitted(store):
    r = run_law_article(store, "건축법", "제1조", chain="none")
    assert "생략" not in r["facts"]


def test_parcel_names_the_regulations_it_left_out():
    others = [f"규제{i}구역" for i in range(15)]
    p = Parcel("경상북도 영천시 금호읍 신월리 1-51", "1", "경상북도", "영천시", ["자연녹지지역"], others)
    r = run_parcel_zoning(p.address, lookup=lambda a: p)
    shown = r["facts"]["기타규제"]
    assert len(shown) < 15 and all(n in r["facts"]["생략"]["기타규제"] for n in others[len(shown):])
