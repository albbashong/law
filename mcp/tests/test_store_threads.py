"""Store 는 MCP 도구가 여러 작업 스레드에서 동시에 부른다(anyio.to_thread) — 연결 하나를 같이 쓰면 sqlite 가
'bad parameter or other API misuse' 를 내고 law_search 가 실패한다(실측 2026-11-19 두 에이전트 동시 질문)."""
from concurrent.futures import ThreadPoolExecutor


def test_store_survives_concurrent_queries(fixture_db):
    from law_mcp.store.db import Store
    store = Store(fixture_db)
    words = [f"{c}{i}" for c in "건축허가도로지역" for i in range(40)]

    def work(k):
        for w in words[k::8]:
            store.title_df(w)
            store.index_grams(w[:2])
            store._all("SELECT doc_id, name FROM doc LIMIT 50")
        return True
    with ThreadPoolExecutor(8) as pool:
        assert all(pool.map(work, range(8)))
