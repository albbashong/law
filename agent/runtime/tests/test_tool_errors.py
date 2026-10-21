"""도구 오류 — 되먹임·반복 차단·errors 보고 (스펙 §5.5, 규격 4.3절)."""

import asyncio

import httpx

import loop
from a2a.store import TaskStore
from a2a.types import TaskState
from config import settings
from executor import Executor


class ToolError(Exception):
    """도구가 실패를 보고했다(fastmcp 의 ToolError 자리)."""


class FakeMcp:
    def __init__(self, behaviour):
        self.behaviour = behaviour          # name → callable(args) | exception
        self.calls = []

    async def list_tools(self):
        return [{"name": n, "description": n, "input_schema": {}} for n in self.behaviour]

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        b = self.behaviour[name]
        if isinstance(b, BaseException):
            raise b
        return b(args)


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def next_step(self, messages, tools):
        self.seen.append(list(messages))
        return self.script.pop(0)


async def test_tool_error_is_fed_back_and_loop_continues():
    mcp = FakeMcp({"survey": ToolError("주소를 찾지 못했습니다: 없는동 1")})
    model = FakeModel([{"tool": "survey", "args": {"query": "없는동 1"}},
                       {"answer": "그 주소는 찾지 못했습니다."}])
    raw = await loop.run_query("없는동 1 조회", mcp=mcp, model=model)
    assert raw["stopped"] == "answer"
    fed = model.seen[1][-1]
    assert fed["role"] == "tool" and '"success": false' in fed["content"]
    assert "주소를 찾지 못했습니다" in fed["content"]
    assert raw["errors"] == [{"tool": "survey", "kind": "tool_error",
                              "message": "주소를 찾지 못했습니다: 없는동 1",
                              "recoverable": True, "attempts": 1}]


async def test_success_after_error_clears_that_tools_error():
    state = {"n": 0}

    def flaky(args):
        state["n"] += 1
        if state["n"] == 1:
            raise ToolError("첫 시도 실패")
        return {"status": "ok", "facts": {"a": 1}}

    class Mcp(FakeMcp):
        async def call(self, name, args):
            self.calls.append((name, dict(args)))
            return flaky(args)

    mcp = Mcp({"survey": flaky})
    model = FakeModel([{"tool": "survey", "args": {"query": "a"}},
                       {"tool": "survey", "args": {"query": "a", "input_type": "auto"}},
                       {"answer": "됐다"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["errors"] == [] and raw["tool_results"]["survey"]["status"] == "ok"


async def test_recalling_a_tool_moves_it_to_the_end_of_tool_results():
    """shape.clarify_source 는 '가장 최근 결과' 를 키 순서로 고른다 — 다시 부른 도구는 뒤로 간다."""
    mcp = FakeMcp({"a": lambda args: {"status": "ok", "n": args.get("n")},
                   "b": lambda args: {"status": "ok"}})
    model = FakeModel([{"tool": "a", "args": {"n": 1}}, {"tool": "b", "args": {}},
                       {"tool": "a", "args": {"n": 2}}, {"answer": "끝"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert list(raw["tool_results"]) == ["b", "a"]
    assert raw["tool_results"]["a"]["n"] == 2


async def test_repeated_identical_call_is_blocked(monkeypatch):
    monkeypatch.setattr(settings, "tool_retry_max", 2)
    mcp = FakeMcp({"survey": ToolError("안 된다")})
    model = FakeModel([{"tool": "survey", "args": {"query": "a"}},
                       {"tool": "survey", "args": {"query": "a"}},
                       {"tool": "survey", "args": {"query": "a"}},
                       {"answer": "포기"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert len(mcp.calls) == 2                                   # 세 번째는 부르지 않았다
    blocked = model.seen[3][-1]["content"]
    assert "같은 호출을 반복" in blocked and "survey" in blocked and "3회" in blocked
    assert raw["errors"][0]["attempts"] == 2


def test_args_hash_is_order_insensitive():
    assert loop.args_hash({"a": 1, "b": 2}) == loop.args_hash({"b": 2, "a": 1})
    assert loop.args_hash({"a": 1}) != loop.args_hash({"a": 2})


async def test_connection_failure_stops_immediately():
    mcp = FakeMcp({"survey": httpx.ConnectError("All connection attempts failed")})
    model = FakeModel([{"tool": "survey", "args": {"query": "a"}},
                       {"answer": "여기 오면 안 된다"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "unavailable" and raw["answer"] == ""
    assert len(model.script) == 1                               # 모델을 다시 부르지 않았다
    assert raw["errors"] == [{"tool": "survey", "kind": "unavailable",
                              "message": "All connection attempts failed",
                              "recoverable": False, "attempts": 1}]


async def test_server_unreachable_at_listing_is_unavailable():
    class Dead:
        async def list_tools(self):
            raise loop.McpUnavailable("parcel", ConnectionError("refused"))

        async def call(self, name, args):
            raise AssertionError("부르면 안 된다")

    raw = await loop.run_query("q", mcp=Dead(), model=FakeModel([]))
    assert raw["stopped"] == "unavailable"
    assert raw["errors"][0]["tool"] == "parcel" and raw["errors"][0]["kind"] == "unavailable"


def test_is_unavailable_classification():
    assert loop.is_unavailable(httpx.ConnectError("x"))
    assert loop.is_unavailable(ConnectionRefusedError())
    assert loop.is_unavailable(TimeoutError())
    assert loop.is_unavailable(ExceptionGroup("g", [httpx.ConnectTimeout("t")]))
    assert not loop.is_unavailable(ToolError("도구가 거절"))
    assert not loop.is_unavailable(ValueError("인자"))


async def _wait(store, tid, state, tries=100):
    for _ in range(tries):
        if store.get(tid).status.state is state:
            return store.get(tid)
        await asyncio.sleep(0.005)
    raise AssertionError(f"{state} 에 못 닿았다: {store.get(tid).status.state}")


def _shape(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
            "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}


async def test_executor_publishes_errors_artifact_with_answer():
    store = TaskStore()

    async def rq(query, **kw):
        return {"answer": "면적은 알아냈지만 접도는 못 봤습니다.", "tool_results": {},
                "stopped": "answer", "messages": [], "usage": {},
                "errors": [{"tool": "parcel_spatial_survey", "kind": "tool_error",
                            "message": "400", "recoverable": True, "attempts": 2}]}

    ex = Executor(store, run_query=rq, shape_result=_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "q", resuming=False)
    done = await _wait(store, task.id, TaskState.COMPLETED)
    names = {a.name for a in done.artifacts}
    assert names == {"answer", "errors", "memory"}
    errors = next(a for a in done.artifacts if a.name == "errors").parts[0].data
    assert errors[0]["tool"] == "parcel_spatial_survey"


async def test_executor_fails_with_reason_when_unavailable():
    store = TaskStore()

    async def rq(query, **kw):
        return {"answer": "", "tool_results": {}, "stopped": "unavailable", "messages": [],
                "usage": {}, "errors": [{"tool": "parcel", "kind": "unavailable",
                                         "message": "refused", "recoverable": False,
                                         "attempts": 1}]}

    ex = Executor(store, run_query=rq, shape_result=_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "q", resuming=False)
    failed = await _wait(store, task.id, TaskState.FAILED)
    text = failed.status.message.text()
    assert "parcel" in text and "refused" in text and text != "실패했습니다"
    assert any(a.name == "errors" for a in failed.artifacts)


async def test_unavailable_wins_over_a_carried_clarify_shape():
    """M1 — 재개로 들어온 애매한 tool_results 가 성형에서 다시 clarify 로 보여도,
    이번 라운드가 도구에 못 닿았으면 되묻지 않고 FAILED 로 끝낸다."""
    store = TaskStore()

    async def rq(query, **kw):
        return {"answer": "", "tool_results": {}, "stopped": "unavailable", "messages": [],
                "usage": {}, "errors": [{"tool": "parcel", "kind": "unavailable",
                                         "message": "refused", "recoverable": False,
                                         "attempts": 1}]}

    def shape_still_clarify(answer, tool_results, usage=None):
        return {"message": "후보가 있습니다.", "status": "clarify",
                "candidates": [{"pnu": "1", "label": "가"}], "resolve": None,
                "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}

    ex = Executor(store, run_query=rq, shape_result=shape_still_clarify)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "q", resuming=True)
    failed = await _wait(store, task.id, TaskState.FAILED)
    text = failed.status.message.text()
    assert "parcel" in text and "refused" in text


class _FakeTool:
    def __init__(self, name):
        self.name, self.description, self.inputSchema = name, name, {}


class _AliveOrDeadClient:
    """fastmcp.Client 흉내 — url 에 'dead' 가 있으면 연결에 실패한다."""

    def __init__(self, url):
        self.url = url

    async def __aenter__(self):
        if "dead" in self.url:
            raise ConnectionError("refused")
        return self

    async def __aexit__(self, *args):
        return None

    async def list_tools(self):
        return [_FakeTool("parcel_land_survey")]

    async def call_tool(self, name, args):
        class Result:
            structured_content = {"status": "ok", "facts": {"지목": "대"}}

        return Result()


async def test_dead_server_does_not_block_the_others(monkeypatch):
    """스펙 §9.7 — spatial 이 죽어도 parcel 만으로 답하고, 죽은 서버는 errors 로 보고한다."""
    import fastmcp

    monkeypatch.setattr(fastmcp, "Client", _AliveOrDeadClient)
    adapter = loop.McpAdapter({"parcel": "http://alive/mcp", "spatial": "http://dead/mcp"}, {})
    model = FakeModel([{"tool": "parcel_land_survey", "args": {"query": "x"}},
                       {"answer": "지목은 대입니다."}])
    raw = await loop.run_query("지목", mcp=adapter, model=model)
    assert raw["stopped"] == "answer" and raw["answer"] == "지목은 대입니다."
    assert set(adapter.unavailable()) == {"spatial"}
    assert raw["errors"] == [{"tool": "spatial", "kind": "unavailable", "message": "refused",
                              "recoverable": False, "attempts": 1}]
    await adapter.aclose()


async def test_all_servers_dead_is_unavailable(monkeypatch):
    import fastmcp

    monkeypatch.setattr(fastmcp, "Client", _AliveOrDeadClient)
    adapter = loop.McpAdapter({"parcel": "http://dead/mcp"}, {})
    raw = await loop.run_query("q", mcp=adapter, model=FakeModel([]))
    assert raw["stopped"] == "unavailable"
    assert raw["errors"][0]["tool"] == "parcel" and raw["errors"][0]["kind"] == "unavailable"
