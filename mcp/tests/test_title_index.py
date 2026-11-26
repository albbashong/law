"""제목 색인은 재시작 뒤 첫 law_search 에서 만든다 — unit·annex 를 본문째 훑으면 9GB 를 읽어 80초가 걸리고,
계획 에이전트가 60초 기다리다 계획 없이 간다(실측 2026-11-19). 제목만 든 인덱스로 읽고, 서버가 뜰 때 미리 만든다."""
import sqlite3
import time


def test_title_rows_are_read_from_covering_indexes(fixture_db):
    from law_mcp.store.db import TITLE_SOURCES
    con = sqlite3.connect(fixture_db)
    for sql in TITLE_SOURCES:
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql))
        assert "COVERING INDEX" in plan, plan


def test_app_builds_the_title_index_at_startup(fixture_db, monkeypatch):
    monkeypatch.setenv("LAW_DB_PATH", str(fixture_db))
    from starlette.testclient import TestClient
    from law_mcp.server import build_app, get_store
    with TestClient(build_app()):
        st = get_store()
        for _ in range(100):
            if st._tix is not None:
                break
            time.sleep(0.05)
        assert st._tix is not None
