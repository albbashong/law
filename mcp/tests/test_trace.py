import json


def test_trace_writes_jsonl(tmp_path, monkeypatch):
    f = tmp_path / "trace.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    res = {"status": "ok", "facts": {"refs": [{"law": "건축법", "article": "제11조"}]}, "message": "m"}
    assert trace("law_article", {"law": "건축법"}, res) is res
    row = json.loads(f.read_text().splitlines()[0])
    assert row["tool"] == "law_article" and row["status"] == "ok"
    assert row["refs"] == [["건축법", "제11조"]]


def test_trace_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LAW_MCP_TRACE_FILE", raising=False)
    from law_mcp.tools.common import trace
    assert trace("x", {}, {"status": "ok"}) == {"status": "ok"}


def test_trace_includes_article_start_and_search_rows(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("law_article", {}, {"status": "ok", "facts": {"법령": "건축법", "조문": "제11조", "refs": []}})
    trace("law_search", {}, {"status": "ok", "facts": {"결과": [{"law": "A 조례", "article": "제4조"}]}})
    rows = [json.loads(x) for x in f.read_text().splitlines()]
    assert rows[0]["refs"] == [["건축법", "제11조"]] and rows[1]["refs"] == [["A 조례", "제4조"]]


def test_trace_records_condition_texts(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("building_basis", {}, {"status": "ok", "facts": {"조건": ["조례 별표 16 확인"], "추가검토": ["산업집적법 제13조"]}})
    row = json.loads(f.read_text())
    assert "조례 별표 16" in row["texts"] and "제13조" in row["texts"]


def test_trace_records_message_on_failure(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("building_basis", {}, {"status": "failed", "facts": {}, "message": "브이월드 실패"})
    assert json.loads(f.read_text())["message"] == "브이월드 실패"


def test_trace_texts_include_per_zone_conditions(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("building_basis", {}, {"status": "ok", "facts": {"용도지역별": [{"조건": ["조례 제33조 확인"]}]}})
    assert "제33조" in json.loads(f.read_text())["texts"]


def test_trace_texts_include_original_and_more(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("law_article", {}, {"status": "ok", "facts": {"원문": "제14조에 따른 신고", "더_볼_연결": [
        {"law": "건축법 시행령", "article": "제119조의11", "이유": "x"}]}})
    t = json.loads(f.read_text())["texts"]
    assert "제14조" in t and "제119조의11" in t


def test_trace_texts_include_use_label_and_ref_excerpts(tmp_path, monkeypatch):
    f = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(f))
    from law_mcp.tools.common import trace
    trace("building_basis", {}, {"status": "ok", "facts": {
        "건축물용도": "위락시설(건축법 시행령 별표 1 제16호)",
        "refs": [{"law": "건축법 시행규칙", "article": "제9조의2", "excerpt": "별표 3의 도서"}]}})
    t = json.loads(f.read_text())["texts"]
    assert "별표 1 제16호" in t and "별표 3" in t


def test_trace_includes_definitions_and_deemed_list(tmp_path, monkeypatch):
    from law_mcp.tools.common import trace
    p = tmp_path / "t.jsonl"
    monkeypatch.setenv("LAW_MCP_TRACE_FILE", str(p))
    trace("law_definition", {}, {"status": "ok", "facts": {"정의": [
        {"law": "국토의 계획 및 이용에 관한 법률", "article": "제2조제15호", "text": "\"용도지역\"이란 …"}]}})
    trace("law_article", {}, {"status": "ok", "facts": {"법령": "건축법", "조문": "제11조제5항",
                                                        "의제_목록": ["제3호 국토의 계획 및 이용에 관한 법률 제56조"]}})
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert ["국토의 계획 및 이용에 관한 법률", "제2조제15호"] in rows[0]["refs"]
    assert "용도지역" in rows[0]["texts"]
    assert "제56조" in rows[1]["texts"]
