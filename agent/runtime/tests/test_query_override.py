"""설정 디렉터리의 query.py — 모델 루프 대신 그 run_query 로 질의를 처리한다(정해진 순서의 에이전트용)."""

import asyncio

from executor import load_agent_query


def test_query_py_in_agent_dir_replaces_the_loop(tmp_path):
    (tmp_path / "query.py").write_text(
        "async def run_query(query, **kw):\n"
        "    return {'answer': 'x:' + query, 'tool_results': {}, 'stopped': 'answer', 'usage': {}}\n",
        encoding="utf-8")
    fn = load_agent_query(tmp_path)
    assert fn is not None
    assert asyncio.run(fn("q"))["answer"] == "x:q"


def test_no_query_py_keeps_the_loop(tmp_path):
    assert load_agent_query(tmp_path) is None
