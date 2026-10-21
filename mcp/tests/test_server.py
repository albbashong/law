from starlette.testclient import TestClient


def test_health_ok(fixture_db, monkeypatch):
    monkeypatch.setenv("LAW_DB_PATH", str(fixture_db))
    from law_mcp.server import build_app
    with TestClient(build_app()) as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json()["docs"] >= 4


def test_health_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LAW_DB_PATH", str(tmp_path / "none.sqlite"))
    from law_mcp.server import build_app
    with TestClient(build_app()) as c:
        assert c.get("/health").status_code == 503


def test_store_basic(store):
    did = store.docs_by_key("국토의계획및이용에관한법률시행령")[0]["doc_id"]
    assert store.unit(did, "제84조/1/16")["label"] == "제84조제1항제16호"
    assert store.zone("자연녹지지역")["annex_no"] == "17"
    assert store.annex(did, "17")["title"].startswith("자연녹지지역")
