"""검색 방식 고르기(SEARCH_METHOD) — trigram(기본: 세 글자 조각) · words(형태소 낱말 + 붙인 복합어, 제목 가중 bm25) ·
hybrid(둘을 순위 융합). 실측 2026-11-06 자동 평가 300문항(흔한 낱말 섞은 검색어): 10위 안 trigram 77% · hybrid 91%."""
import pytest

from law_mcp.config import Settings
from law_mcp.engine import words as w
from law_mcp.tools import search


def _use(monkeypatch, method, path):
    s = Settings(search_method=method, words_db_path=path)
    monkeypatch.setattr(search, "get_settings", lambda: s)
    monkeypatch.setattr(w, "_index", {})


def test_compound_nouns_are_kept_with_their_parts():
    # 분석기는 "건축신고"를 "건축 + 신고"로 쪼갠다 — 붙인 복합어도 남겨야 드문 말이 흔한 말로 흩어지지 않는다
    toks = w.words("건축신고 대상")
    assert {"건축", "신고", "건축신고", "대상"} <= set(toks)


def test_a_single_particle_between_nouns_does_not_break_the_compound():
    # 법령 제목은 "도로의 점용 허가"처럼 조사로 끊어 쓰고 검색어는 "도로점용허가"로 붙여 쓴다 — 명사 사이 조사 하나(품사 J*)는
    # 건너뛰어 잇는다. 조사 글자가 아니라 품사로 본다
    assert "도로점용허가" in w.words("도로의 점용 허가")
    assert "농지전용허가" in w.words("농지의 전용허가")
    assert "도로점용허가" in w.words("도로점용허가")


def test_words_search_finds_the_article_titled_by_the_compound(monkeypatch, store, words_db):
    _use(monkeypatch, "words", words_db)
    rows = search.run_law_search(store, "건축신고 대상 면적 기준", scope="law")["facts"]["결과"]
    assert ("건축법", "제14조") in [(r["law"], r["article"]) for r in rows[:5]]
    assert all("호출인자" in r for r in rows)


def test_words_search_keeps_the_scope(monkeypatch, store, words_db):
    _use(monkeypatch, "words", words_db)
    rows = search.run_law_search(store, "건축신고", scope="law")["facts"]["결과"]
    assert rows and not any("조례" in r["law"] for r in rows)


def test_hybrid_merges_both_lists(monkeypatch, store, words_db):
    _use(monkeypatch, "trigram", words_db)
    tri = {(r["law"], r["article"]) for r in search.run_law_search(store, "건축신고 대상 면적 기준")["facts"]["결과"]}
    _use(monkeypatch, "words", words_db)
    wor = {(r["law"], r["article"]) for r in search.run_law_search(store, "건축신고 대상 면적 기준")["facts"]["결과"]}
    _use(monkeypatch, "hybrid", words_db)
    hyb = {(r["law"], r["article"]) for r in search.run_law_search(store, "건축신고 대상 면적 기준")["facts"]["결과"]}
    assert hyb & (tri - wor) and hyb & (wor - tri)


def test_missing_words_index_falls_back_to_trigram(monkeypatch, store, tmp_path):
    _use(monkeypatch, "trigram", tmp_path / "none.sqlite")
    tri = search.run_law_search(store, "건축신고")["facts"]["결과"]
    _use(monkeypatch, "words", tmp_path / "none.sqlite")
    assert search.run_law_search(store, "건축신고")["facts"]["결과"] == tri


def test_split_sources_keeps_laws_from_being_buried_by_ordinances(monkeypatch, store, words_db):
    # 조례가 법령보다 훨씬 많아 제목이 비슷한 시군 조례가 법령 조문을 덮는다 — 법령 몇 개·조례 몇 개로 나눠 담는다(출처 값으로)
    s = Settings(search_method="trigram", words_db_path=words_db, search_split_sources=True)
    monkeypatch.setattr(search, "get_settings", lambda: s)
    rows = search.run_law_search(store, "건축신고", limit=4)["facts"]["결과"]
    src = ["ordin" if store.doc_named(r["law"])["source"] == "ordin" else "law" for r in rows]
    assert src[:src.count("law")] == ["law"] * src.count("law")      # 법령 묶음이 먼저
    assert 0 < src.count("law") <= 4 and src.count("ordin") <= 4
