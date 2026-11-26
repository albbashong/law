"""뜻으로 찾기(SEARCH_METHOD 에 vector) — 조문을 임베딩해 두고 검색어 임베딩과 가까운 조문을 찾는다.

글자 조각·낱말 색인은 검색어와 조문이 같은 말을 써야 찾는다. 일상어("해외주식으로 번 돈")로 물으면 법령 용어("양도소득")로
쓰인 조문에 닿지 못했다(실측 2026-11-19 pg). 자동 평가(일상어 질문 270개, 2026-11-17): 10위 안 trigram 14% · 임베딩 78%.
테스트는 임베딩 모델 대신 정해 둔 벡터를 쓴다 — 모델이 아니라 색인·검색·융합의 동작을 본다."""
import time

import numpy as np

from law_mcp.config import Settings
from law_mcp.engine import vectors as v
from law_mcp.tools import search

DIM = 8


def _axis(i):
    x = np.zeros(DIM, dtype=np.float32)
    x[i] = 1.0
    return x


def fake_embed(texts, *, query=False):
    """조문 머리글로 축을 정한다 — 농지법 제34조(농지의 전용허가)는 축 0, 건축법 제11조는 축 1, 나머지는 축 7 근처."""
    out = []
    for t in texts:
        if ("농지법 제34조" in t and not query) or "밭에 창고" in t:
            out.append(_axis(0))
        elif ("건축법 제11조" in t and not query) or "집 짓는 허락" in t:
            out.append(_axis(1))
        else:
            out.append(_axis(7) + 0.01 * _axis(len(t) % 6 + 2))
    return np.stack(out)


def _use(monkeypatch, method, path):
    s = Settings(search_method=method, vector_dir=path)
    monkeypatch.setattr(search, "get_settings", lambda: s)
    monkeypatch.setattr(v, "get_settings", lambda: s)
    monkeypatch.setattr(v, "_loaded", {})
    monkeypatch.setattr(v, "embed", fake_embed)


def _build(fixture_db, tmp_path, monkeypatch):
    monkeypatch.setattr(v, "embed", fake_embed)
    out = tmp_path / "vec"
    v.build_vector_index(fixture_db, out, batch=64, log=lambda m: None)
    return out


def test_vector_search_finds_the_article_without_shared_words(monkeypatch, store, fixture_db, tmp_path):
    out = _build(fixture_db, tmp_path, monkeypatch)
    _use(monkeypatch, "vector", out)
    rows = search.run_law_search(store, "밭에 창고 지어도 돼?", scope="law")["facts"]["결과"]
    assert ("농지법", "제34조") == (rows[0]["law"], rows[0]["article"])
    assert rows[0]["호출인자"] == {"law": "농지법", "article": "제34조"}


def test_trigram_and_vector_are_fused(monkeypatch, store, fixture_db, tmp_path):
    out = _build(fixture_db, tmp_path, monkeypatch)
    _use(monkeypatch, "trigram+vector", out)
    rows = search.run_law_search(store, "집 짓는 허락 건축신고", scope="law")["facts"]["결과"]
    got = [(r["law"], r["article"]) for r in rows[:6]]
    assert ("건축법", "제11조") in got          # 뜻으로 찾은 것
    assert ("건축법", "제14조") in got          # 글자로 찾은 것(건축신고)


def test_vector_search_keeps_regional_search_on_ordinances(monkeypatch, store, fixture_db, tmp_path):
    # 색인은 법령 조문만 든다 — 지역을 주면(조례만 찾는다) 뜻 검색은 보태지 않는다
    out = _build(fixture_db, tmp_path, monkeypatch)
    _use(monkeypatch, "trigram+vector", out)
    rows = search.run_law_search(store, "밭에 창고 지어도 돼?", scope="all", region="영천시")["facts"].get("결과", [])
    assert not any(r["law"] == "농지법" for r in rows)


def test_missing_index_falls_back_to_trigram(monkeypatch, store, tmp_path):
    _use(monkeypatch, "trigram+vector", tmp_path / "none")
    base = Settings(search_method="trigram")
    rows = search.run_law_search(store, "건축신고", scope="law")["facts"]["결과"]
    monkeypatch.setattr(search, "get_settings", lambda: base)
    assert rows == search.run_law_search(store, "건축신고", scope="law")["facts"]["결과"]


def test_rebuild_reuses_vectors_of_unchanged_articles(monkeypatch, fixture_db, tmp_path):
    out = _build(fixture_db, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(v, "embed", lambda texts, query=False: seen.extend(texts) or fake_embed(texts))
    v.build_vector_index(fixture_db, out, batch=64, log=lambda m: None)
    assert seen == []                            # 글이 그대로면 다시 임베딩하지 않는다


def test_hybrid_still_means_trigram_and_words():
    assert search._rankers("hybrid") == ["trigram", "words"]
    assert search._rankers("trigram+vector") == ["trigram", "vector"]
    assert search._rankers("trigram") == ["trigram"]


def test_app_loads_the_vector_index_at_startup(monkeypatch, fixture_db, tmp_path):
    # 색인(약 0.8GB)을 첫 검색에서 읽으면 그 질문이 기다린다 — 서버가 뜰 때 미리 읽는다
    out = _build(fixture_db, tmp_path, monkeypatch)
    monkeypatch.setenv("LAW_DB_PATH", str(fixture_db))
    monkeypatch.setenv("SEARCH_METHOD", "trigram+vector")
    monkeypatch.setenv("VECTOR_DIR", str(out))
    monkeypatch.setattr(v, "_loaded", {})
    from starlette.testclient import TestClient
    from law_mcp.server import build_app
    with TestClient(build_app()):
        for _ in range(100):
            if str(out) in v._loaded:
                break
            time.sleep(0.05)
        assert v._loaded.get(str(out)) is not None


def test_index_built_from_another_db_build_is_not_used(fixture_db, store, tmp_path, monkeypatch):
    # DB 를 다시 만들면 조문 번호(unit_id)가 새로 매겨진다 — 앞 빌드의 색인으로 찾으면 다른 조문이 나온다
    out = _build(fixture_db, tmp_path, monkeypatch)
    assert v.search(out, "밭에 창고 지어도 돼?", 3, source=store.built_at())
    monkeypatch.setattr(v, "_loaded", {})
    assert v.search(out, "밭에 창고 지어도 돼?", 3, source="1999-01-01 00:00:00") is None
