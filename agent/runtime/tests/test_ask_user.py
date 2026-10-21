"""후보 없는 되묻기 — 모델이 `ask_user` 를 부르면 MCP 를 부르지 않고 INPUT_REQUIRED (스펙 §5.4)."""

import asyncio

import loop
from a2a.store import TaskStore
from a2a.types import TaskState
from executor import Executor


class FakeMcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "resolve_location", "description": "위치", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"status": "ok"}


class FakeModel:
    """대본대로 답한다. 각 항목은 {"tool": …, "args": …} 또는 {"answer": …}."""

    def __init__(self, script):
        self.script = list(script)
        self.seen_tools = []
        self.seen_messages = []

    async def next_step(self, messages, tools):
        self.seen_tools.append([t["name"] for t in tools])
        self.seen_messages.append(list(messages))
        return self.script.pop(0)


async def test_ask_user_is_offered_alongside_mcp_tools():
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("q", mcp=FakeMcp(), model=model)
    assert model.seen_tools[0] == ["resolve_location", loop.ASK_USER]


async def test_ask_user_stops_the_loop_without_calling_mcp():
    mcp = FakeMcp()
    model = FakeModel([{"tool": loop.ASK_USER,
                        "args": {"question": "어느 주소입니까?",
                                 "candidates": [{"label": "첫째"}, {"id": "b", "label": "둘째"},
                                                {"nolabel": True}]}}])
    raw = await loop.run_query("위치 보여줘", mcp=mcp, model=model)
    assert mcp.calls == []
    assert raw["stopped"] == "ask" and raw["answer"] == ""
    assert raw["ask"]["question"] == "어느 주소입니까?"
    assert raw["ask"]["candidates"] == [{"label": "첫째", "id": "0"}, {"id": "b", "label": "둘째"}]
    # 다음 턴이 이어받을 대화에 도구 호출과 그 응답이 짝으로 남는다 — 짝이 없으면
    # 모델 API 가 거부한다
    roles = [m["role"] for m in raw["messages"]]
    assert roles[-2:] == ["assistant", "tool"]


async def test_empty_question_is_fed_back_and_loop_continues():
    mcp = FakeMcp()
    model = FakeModel([{"tool": loop.ASK_USER, "args": {"question": "  "}},
                       {"answer": "그럼 답합니다"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "answer" and raw["answer"] == "그럼 답합니다"
    assert "question" in model.seen_messages[1][-1]["content"]


async def test_ask_user_leaves_no_unpaired_tool_calls_in_a_multi_call_round():
    mcp = FakeMcp()
    model = FakeModel([{"tools": [{"id": "c1", "name": loop.ASK_USER,
                                   "args": {"question": "어디입니까?"}},
                                  {"id": "c2", "name": "resolve_location",
                                   "args": {"query": "x"}}]},
                       {"answer": "끝"}])
    raw = await loop.run_query("q", mcp=mcp, model=model)
    assert raw["stopped"] == "ask" and mcp.calls == []
    assistant = next(m for m in raw["messages"] if m["role"] == "assistant")
    answered = {m["tool_call_id"] for m in raw["messages"] if m["role"] == "tool"}
    assert {tc["id"] for tc in assistant["tool_calls"]} == answered == {"c1", "c2"}


def test_ask_from_normalises_candidates():
    out = loop.ask_from({"question": "q", "candidates": "not a list"})
    assert out == {"question": "q", "candidates": []}


async def _wait(store, tid, state, tries=100):
    for _ in range(tries):
        if store.get(tid).status.state is state:
            return store.get(tid)
        await asyncio.sleep(0.005)
    raise AssertionError(f"{state} 에 못 닿았다: {store.get(tid).status.state}")


async def test_executor_turns_ask_into_input_required_and_resumes():
    store = TaskStore()
    calls = []

    async def fake_run_query(query, *, on_progress=None, prior=None, **kw):
        calls.append((query, prior))
        if len(calls) == 1:
            return {
                "answer": "",
                "ask": {"question": "주소를 알려 주세요.",
                        "candidates": [{"id": "0", "label": "경산시청"}]},
                "tool_results": {}, "stopped": "ask",
                "messages": [{"role": "user", "content": query}],
                "usage": {"tokens_in": 5, "tokens_out": 1}
            }
        return {"answer": "경산시청으로 옮겼습니다.", "tool_results": {}, "stopped": "answer",
                "messages": [], "usage": {"tokens_in": 7, "tokens_out": 2}}

    def fake_shape(answer, tool_results, usage=None):
        return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
                "actions": [], "tool_results": {}, "usage": dict(usage or {})}

    ex = Executor(store, run_query=fake_run_query, shape_result=fake_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "지도 옮겨줘", resuming=False)
    asked = await _wait(store, task.id, TaskState.INPUT_REQUIRED)
    assert asked.status.message.text() == "주소를 알려 주세요."
    assert next(a for a in asked.artifacts if a.name == "candidates").parts[0].data == \
        [{"id": "0", "label": "경산시청"}]
    assert asked.metadata[loop_usage_key()]["tokensIn"] == 5

    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "경산시청", resuming=True)
    done = await _wait(store, task.id, TaskState.COMPLETED)
    assert "원래 요청: 지도 옮겨줘" in calls[1][0] and "주소를 알려 주세요." in calls[1][0]
    assert done.status.message.text() == "경산시청으로 옮겼습니다."


def loop_usage_key():
    from a2a.extensions import USAGE
    return USAGE
