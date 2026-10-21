"""작업 기억 — 답과 함께 'memory' 아티팩트를 내고, 다음 호출의 data 파트 memory 를 이전 작업으로 읽는다.

에이전트는 상태를 들지 않는 것이 원칙이다. 호출자(오케스트레이터)가 이 아티팩트를 (대화, 에이전트) 칸에
보관했다가 같은 대화의 다음 호출에 data 파트로 그대로 돌려준다. 호출자가 아직 그러지 못하는 동안은
런타임이 contextId 별로 잠깐 들고 있는다(MEMORY_LOCAL).
"""

import asyncio

import httpx

import loop
from a2a.server import build_app
from a2a.store import TaskStore
from a2a.types import TaskState, new_id
from config import settings
from executor import Executor

CARD_SPEC = {
    "name": "테스트 에이전트", "description": "법령을 찾는다.", "version": "1.0.0",
    "skills": [{"id": "law", "name": "법령", "description": "법령을 찾는다.", "tags": ["법령"],
                "examples": ["수의계약 조건"], "hints": {"readOnly": True, "idempotent": True}}],
}


def done(answer="답", calls=None):
    return {"answer": answer, "tool_results": {}, "stopped": "answer", "usage": {}, "errors": [],
            "messages": [], "calls": calls or []}


def shape_passthrough(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None, "actions": [],
            "tool_results": dict(tool_results), "usage": dict(usage or {})}


class Recorder:
    def __init__(self, *outcomes):
        self.calls, self.outcomes = [], list(outcomes)

    async def __call__(self, query, *, on_progress=None, prior=None, context=None):
        self.calls.append({"query": query, "context": context})
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)] if self.outcomes else done()


def client_for(run_query):
    store = TaskStore()
    ex = Executor(store, run_query=run_query, shape_result=shape_passthrough)
    app = build_app(store=store, executor=ex, card_spec=CARD_SPEC, url="http://test")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"A2A-Version": "1.0"}), store


def body(parts, **kw):
    return {"message": {"messageId": new_id("m_"), "role": "ROLE_USER", "parts": parts, **kw},
            "configuration": {"returnImmediately": False}}


async def finish(client, store, parts, **kw):
    r = await client.post("/message:send", json=body(parts, **kw))
    task_id = r.json()["task"]["id"]
    for _ in range(100):
        t = store.get(task_id)
        if t.status.state is TaskState.COMPLETED:
            return t
        await asyncio.sleep(0.01)
    raise AssertionError(store.get(task_id).status.state)


def memory_of(task):
    return next(a for a in task.artifacts if a.name == "memory").parts[0].data


CALL = {"tool": "law_article", "args": {"law": "국가를 당사자로 하는 계약에 관한 법률 시행령", "article": "제26조"}}


async def test_completed_task_publishes_memory_of_the_turn():
    client, store = client_for(Recorder(done("수의계약은 시행령 제26조가 정합니다.", [CALL])))
    async with client:
        task = await finish(client, store, [{"text": "수의계약 조건"}])
    turns = memory_of(task)["turns"]
    assert turns == [{"q": "수의계약 조건", "a": "수의계약은 시행령 제26조가 정합니다.", "calls": [CALL]}]


async def test_memory_from_data_part_is_passed_on_and_extended(monkeypatch):
    monkeypatch.setattr(settings, "memory_turns", 2)
    prev = {"turns": [{"q": "가", "a": "1", "calls": []}, {"q": "나", "a": "2", "calls": []}]}
    run_query = Recorder(done("3"))
    client, store = client_for(run_query)
    async with client:
        task = await finish(client, store, [{"text": "다"}, {"data": {"memory": prev}}])
    assert run_query.calls[0]["context"]["memory"] == prev
    assert [t["q"] for t in memory_of(task)["turns"]] == ["나", "다"]      # 최근 2턴만


async def test_local_fallback_carries_memory_within_a_context(monkeypatch):
    monkeypatch.setattr(settings, "memory_local", True)
    run_query = Recorder(done("첫 답"), done("둘째 답"))
    client, store = client_for(run_query)
    async with client:
        await finish(client, store, [{"text": "수의계약 조건"}], contextId="ctx-1")
        await finish(client, store, [{"text": "금액은?"}], contextId="ctx-1")
        await finish(client, store, [{"text": "다른 대화"}], contextId="ctx-2")
    assert run_query.calls[1]["context"]["memory"]["turns"][0]["q"] == "수의계약 조건"
    assert not (run_query.calls[2]["context"] or {}).get("memory")


async def test_local_fallback_off(monkeypatch):
    monkeypatch.setattr(settings, "memory_local", False)
    run_query = Recorder(done("첫 답"), done("둘째 답"))
    client, store = client_for(run_query)
    async with client:
        await finish(client, store, [{"text": "수의계약 조건"}], contextId="ctx-1")
        await finish(client, store, [{"text": "금액은?"}], contextId="ctx-1")
    assert not (run_query.calls[1]["context"] or {}).get("memory")


async def test_data_part_memory_wins_over_local(monkeypatch):
    monkeypatch.setattr(settings, "memory_local", True)
    sent = {"turns": [{"q": "호출자가 준 것", "a": "", "calls": []}]}
    run_query = Recorder(done("첫 답"), done("둘째 답"))
    client, store = client_for(run_query)
    async with client:
        await finish(client, store, [{"text": "수의계약 조건"}], contextId="ctx-1")
        await finish(client, store, [{"text": "금액은?"}, {"data": {"memory": sent}}], contextId="ctx-1")
    assert run_query.calls[1]["context"]["memory"] == sent


async def test_long_answer_is_trimmed_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "memory_answer_chars", 10)
    client, store = client_for(Recorder(done("가" * 50)))
    async with client:
        task = await finish(client, store, [{"text": "q"}])
    assert len(memory_of(task)["turns"][0]["a"]) <= 10


# ── loop.run_query — 이전 작업은 현재 발화 앞에 따로 놓이고, 인용 근거로는 치지 않는다 ─────

class FakeMcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "law_article", "description": "조문", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제26조"}]}}


class FakeModel:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


MEM = {"turns": [{"q": "수의계약 조건", "a": "시행령 제26조가 정합니다.", "calls": [CALL]}]}


async def test_previous_work_is_shown_before_the_utterance():
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("금액은?", mcp=FakeMcp(), model=model, context={"memory": MEM})
    msgs = model.seen[0]
    assert msgs[-1]["content"].startswith("[현재 질문]") and msgs[-1]["content"].endswith("금액은?")
    assert "수의계약 조건" in msgs[-2]["content"] and "제26조" in msgs[-2]["content"]
    assert "law_article" in msgs[-2]["content"]


async def test_citation_only_in_previous_work_must_be_looked_up_again(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    model = FakeModel([{"answer": "제26조입니다."}, {"answer": "제26조입니다."}])
    raw = await loop.run_query("금액은?", mcp=FakeMcp(), model=model, context={"memory": MEM})
    assert raw["ungrounded"] == ["제26조"]


async def test_run_query_reports_the_calls_it_made():
    mcp = FakeMcp()
    model = FakeModel([{"tool": "law_article", "args": CALL["args"]}, {"answer": "답"}])
    raw = await loop.run_query("수의계약", mcp=mcp, model=model)
    assert raw["calls"] == [CALL]


async def test_utterance_is_unlabeled_without_previous_work():
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("금액은?", mcp=FakeMcp(), model=model)
    assert model.seen[0][-1]["content"] == "금액은?"


async def test_bad_memory_is_ignored():
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("q", mcp=FakeMcp(), model=model, context={"memory": "문자열"})
    assert [m["role"] for m in model.seen[0]] == ["system", "user"]
