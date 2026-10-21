import pytest


@pytest.mark.asyncio
async def test_tools_registered(fixture_db, monkeypatch):
    monkeypatch.setenv("LAW_DB_PATH", str(fixture_db))
    from law_mcp.server import build_server
    tools = {t.name: t for t in await build_server().list_tools()}
    assert {"law_article", "law_search"} <= set(tools)
    for t in tools.values():
        # 플래너는 설명 앞 160자만 본다 — 사용자 표현이 앞에 있어야 한다
        assert len(t.description) > 40
