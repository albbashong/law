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
    blocked = model.seen[3][-2]["content"]                       # 그 뒤에 도구 없이 답하라는 지시가 붙는다
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


class ToolsSeenModel(FakeModel):
    def __init__(self, script):
        super().__init__(script)
        self.tools_seen = []

    async def next_step(self, messages, tools):
        self.tools_seen.append(list(tools))
        return await super().next_step(messages, tools)


async def test_round_of_only_blocked_repeats_is_followed_by_an_answer_round(monkeypatch):
    # 막힌 호출만 되풀이하면 라운드 상한까지 헛돌다 실패한다 — 다음 라운드는 도구 없이 지금까지로 답하게 한다
    monkeypatch.setattr(settings, "tool_retry_max", 2)
    mcp = FakeMcp({"survey": ToolError("못 찾았습니다. 제안: 정식 이름")})
    model = ToolsSeenModel([{"tool": "survey", "args": {"query": "a"}},
                            {"tool": "survey", "args": {"query": "a"}},
                            {"tool": "survey", "args": {"query": "a"}},
                            {"answer": "찾은 범위로 답합니다."}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "answer" and raw["answer"] == "찾은 범위로 답합니다."
    assert model.tools_seen[2] and model.tools_seen[3] == []
    assert settings.repeat_answer_text in model.seen[3][-1]["content"]


async def test_same_result_under_reworded_args_counts_as_a_repeat(monkeypatch):
    # 검색어만 조금씩 바꿔 같은 결과를 되풀이 받으면 라운드 상한까지 헛돈다 — 같은 결과도 반복으로 센다
    monkeypatch.setattr(settings, "tool_retry_max", 2)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    mcp = FakeMcp({"search": lambda a: {"status": "ok", "facts": {"refs": [["도로교통법", "제50조"]]},
                                        "message": f"'{a['query']}' 1건"}})
    model = ToolsSeenModel([{"tool": "search", "args": {"query": "헬멧 범칙금"}},
                            {"tool": "search", "args": {"query": "헬멧 미착용 범칙금"}},
                            {"tool": "search", "args": {"query": "헬멧 착용 의무"}},
                            {"answer": "찾은 범위로 답합니다."}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "answer"
    assert "새로 나온 근거가 없습니다" in model.seen[3][-2]["content"]
    assert model.tools_seen[3] == [] and settings.repeat_answer_text in model.seen[3][-1]["content"]


async def test_last_round_answers_from_what_was_found(monkeypatch):
    # 라운드 상한에 닿으면 실패 대신, 마지막 라운드는 도구 없이 조회한 것으로 답하게 한다
    monkeypatch.setattr(settings, "rounds_max", 3)
    n = {"i": 0}

    def search(a):
        n["i"] += 1
        return {"status": "ok", "facts": {"refs": [["법", f"제{n['i']}조"]]}}
    mcp = FakeMcp({"search": search})
    model = ToolsSeenModel([{"tool": "search", "args": {"query": "a"}},
                            {"tool": "search", "args": {"query": "b"}},
                            {"answer": "여기까지 찾은 것으로 답합니다."}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "answer" and model.tools_seen[2] == []
    assert settings.last_round_text in model.seen[2][-1]["content"]


async def test_ask_after_pick_list_is_sent_back_once():
    # 도구가 "목록에서 골라 다시 부르라"(pick)고 했는데 모델이 사용자에게 고르라 하면 한 번 돌려보낸다
    def build(a):
        if a["use"] == "카페":
            return {"status": "failed", "facts": {"용도목록": ["제3호 가목 휴게음식점"]},
                    "message": "골라 다시 부르세요.", "pick": "용도목록"}
        return {"status": "ok", "facts": {"근거상태": "조례_허용"}, "message": "ok"}
    mcp = FakeMcp({"building_basis": build})
    model = FakeModel([{"tool": "building_basis", "args": {"use": "카페"}},
                       {"tool": "ask_user", "args": {"question": "어느 용도로 볼까요?"}},
                       {"tool": "building_basis", "args": {"use": "제3호가목"}},
                       {"answer": "휴게음식점으로 보고 답합니다."}])
    raw = await loop.run_query("카페 되나요", mcp=mcp, model=model)
    assert raw["stopped"] == "answer"
    assert "용도목록" in model.seen[2][-1]["content"] and model.seen[2][-1]["role"] == "tool"


async def test_second_ask_after_pick_list_reaches_the_user():
    # 돌려보낸 뒤에도 또 물으면 정말 애매한 것 — 사용자에게 묻는다
    mcp = FakeMcp({"building_basis": lambda a: {"status": "failed", "facts": {"용도목록": ["x"]},
                                                "message": "m", "pick": "용도목록"}})
    model = FakeModel([{"tool": "building_basis", "args": {"use": "카페"}},
                       {"tool": "ask_user", "args": {"question": "q1"}},
                       {"tool": "ask_user", "args": {"question": "q2"}}])
    raw = await loop.run_query("카페", mcp=mcp, model=model)
    assert raw["stopped"] == "ask" and raw["ask"]["question"] == "q2"


async def test_answer_missing_a_parcel_regulation_is_sent_back_once():
    # 도구가 답에 꼭 나와야 할 규제 이름(mention)을 주었는데 답에 없으면 한 번 되돌린다
    mcp = FakeMcp({"building_basis": lambda a: {"status": "ok", "facts": {"근거상태": "조례_허용"},
                                                "message": "ok", "mention": ["개발제한구역"]}})
    model = FakeModel([{"tool": "building_basis", "args": {"use": "공장"}},
                       {"answer": "공장은 지을 수 있습니다."},
                       {"answer": "다만 개발제한구역이라 원칙적으로 건축이 제한됩니다."}])
    raw = await loop.run_query("공장 되나요", mcp=mcp, model=model)
    assert raw["stopped"] == "answer" and "개발제한구역" in raw["answer"]
    assert "개발제한구역" in model.seen[2][-1]["content"] and model.seen[2][-1]["role"] == "user"


async def test_answer_still_missing_a_regulation_after_one_return_is_given():
    mcp = FakeMcp({"building_basis": lambda a: {"status": "ok", "facts": {"근거상태": "조례_허용"},
                                                "message": "ok", "mention": ["개발 제한 구역"]}})
    model = FakeModel([{"tool": "building_basis", "args": {"use": "공장"}},
                       {"answer": "공장은 지을 수 있습니다."},
                       {"answer": "공장은 지을 수 있습니다!"}])
    raw = await loop.run_query("공장 되나요", mcp=mcp, model=model)
    assert raw["stopped"] == "answer" and raw["answer"].startswith("공장은 지을 수 있습니다!")
