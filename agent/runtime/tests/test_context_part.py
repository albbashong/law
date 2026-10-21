"""data 파트(코어의 초점) → `run_query(context=…)`. text 파트는 **사용자 원문 그대로** 남는다.

코어의 `ask` 계약은 "사용자 발화 그대로"인데 실제로는 코어 모델이 에이전트별로 문장을 다시
써서 넘긴다("경상북도 경산시 백천동 562-5"). 동사·의도가 사라져 에이전트가 할 일을 못 알아본다.
A2A `Message.parts` 는 text 와 data 를 함께 실을 수 있으니, 코어가 **원문은 text 로, 자기 초점은
data 로** 주면 둘 다 살아난다. 이 파일은 런타임 쪽 계약을 잰다:

  - data 파트가 dict 면 모아서(뒤가 앞을 덮는다) `context` 로 `run_query` 에 넘긴다.
  - 주입된 `run_query` 의 서명에 `context` 가 없으면 넘기지 않는다(하위 호환).
  - 발화 텍스트는 **text 파트만** 이어 붙인 것이다 — data 파트가 섞여 들어가지 않는다.
  - 재개 턴도 같은 규칙이다.
"""

import asyncio
import inspect

import httpx
import pytest

import loop
from a2a.server import build_app
from a2a.store import TaskStore
from a2a.types import TaskState, new_id
from executor import Executor

CARD_SPEC = {
    "name": "테스트 에이전트",
    "description": "위치를 찾는다.",
    "version": "1.0.0",
    "skills": [{"id": "search", "name": "위치검색", "description": "위치를 찾는다.",
                "tags": ["위치"], "examples": ["안양시청 찾아줘"],
                "hints": {"readOnly": True, "idempotent": True}}],
}
UTTERANCE = "경상북도 경산시 백천동 562-5 단독주택 지을 수 있어?"
FOCUS = "백천동 562-5 위치"


def done(answer="답", **extra):
    return {"answer": answer, "tool_results": {}, "stopped": "done",
            "usage": {"tokens_in": 0, "tokens_out": 0}, "errors": [], "messages": [], **extra}


def shape_passthrough(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
            "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}


class Recorder:
    """`context` 를 받는 run_query 대역."""

    def __init__(self, *outcomes):
        self.calls: list[dict] = []
        self.outcomes = list(outcomes)

    async def __call__(self, query, *, on_progress=None, prior=None, context=None):
        self.calls.append({"query": query, "context": context, "prior": prior})
        return self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)] if self.outcomes \
            else done()


class Old:
    """`context` 를 모르는 run_query — 구형 주입도 그대로 돌아야 한다."""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, query, *, on_progress=None, prior=None):
        self.calls.append(query)
        return done()


def client_for(run_query, store=None):
    store = store or TaskStore()
    ex = Executor(store, run_query=run_query, shape_result=shape_passthrough)
    app = build_app(store=store, executor=ex, card_spec=CARD_SPEC, url="http://test")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"A2A-Version": "1.0"}), store


def body(parts, **kw):
    return {"message": {"messageId": new_id("m_"), "role": "ROLE_USER", "parts": parts, **kw},
            "configuration": {"returnImmediately": False}}


async def settled(store, task_id, want=TaskState.COMPLETED, tries=100):
    for _ in range(tries):
        if store.get(task_id).status.state is want:
            return store.get(task_id)
        await asyncio.sleep(0.01)
    raise AssertionError(f"{want.value} 에 닿지 못했다: {store.get(task_id).status.state.value}")


async def test_data_part_reaches_run_query_as_context_and_text_stays_the_utterance():
    run_query = Recorder()
    client, store = client_for(run_query)
    async with client:
        r = await client.post("/message:send", json=body(
            [{"text": UTTERANCE}, {"data": {"focus": FOCUS}}]))
    assert r.status_code == 200
    call = run_query.calls[0]
    assert call["query"] == UTTERANCE            # 원문만 — 초점은 텍스트에 섞이지 않는다
    assert call["context"] == {"focus": FOCUS}


async def test_data_parts_merge_with_the_last_one_winning():
    run_query = Recorder()
    client, _ = client_for(run_query)
    async with client:
        await client.post("/message:send", json=body(
            [{"text": UTTERANCE}, {"data": {"focus": "먼저", "turn": 1}},
             {"data": {"focus": FOCUS}}, {"data": "문자열은 무시"}]))
    assert run_query.calls[0]["context"] == {"focus": FOCUS, "turn": 1}


async def test_no_data_part_means_no_context():
    run_query = Recorder()
    client, _ = client_for(run_query)
    async with client:
        await client.post("/message:send", json=body([{"text": UTTERANCE}]))
    assert run_query.calls[0]["context"] in (None, {})


async def test_run_query_without_context_in_its_signature_is_called_as_before():
    run_query = Old()
    client, store = client_for(run_query)
    async with client:
        r = await client.post("/message:send", json=body(
            [{"text": UTTERANCE}, {"data": {"focus": FOCUS}}]))
    task_id = r.json()["task"]["id"]
    await settled(store, task_id)
    assert run_query.calls == [UTTERANCE]        # 넘기지 않았다 — TypeError 도 나지 않았다


async def test_resume_turn_carries_the_context_too():
    ask = {**done(""), "stopped": "ask", "ask": {"question": "어느 위치입니까?", "candidates": []}}
    run_query = Recorder(ask, done())
    client, store = client_for(run_query)
    async with client:
        first = await client.post("/message:send", json=body([{"text": UTTERANCE}]))
        task_id = first.json()["task"]["id"]
        await settled(store, task_id, TaskState.INPUT_REQUIRED)
        await client.post("/message:send", json=body(
            [{"text": "백천동 562-5"}, {"data": {"focus": FOCUS}}], taskId=task_id))
    second = run_query.calls[1]
    assert second["context"] == {"focus": FOCUS}
    assert second["query"].startswith(f"원래 요청: {UTTERANCE}")     # 재개는 감싼 텍스트다
    assert "사용자 답변: 백천동 562-5" in second["query"]


# ── loop.run_query — 초점은 사용자 메시지 뒤에 한 줄로 붙는다 ────────────────────

class FakeMcp:
    async def list_tools(self):
        return [{"name": "resolve_location", "description": "위치", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok"}


class FakeModel:
    def __init__(self):
        self.seen: list[list[dict]] = []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return {"answer": "답"}


def test_with_focus_carries_the_confirmed_facts_the_core_sent():
    """코어가 data 파트로 넘긴 "이번 턴까지 확인된 사실" 이 모델 메시지에 남아야 한다.

    멀티턴 실측(2026-09-14): 앞 턴에서 확인한 대상을 코어가 문장에 옮겨 적지 않으면, 위치가
    필요한 에이전트가 그것을 모른 채 되묻고 턴이 빈손으로 끝났다(9/60).
    """
    context = {"focus": FOCUS,
               "facts": {"candidates:P1": {"pnu": "P1", "address": "관양동 1023-4"}}}
    text = loop.with_focus(UTTERANCE, context)
    assert UTTERANCE in text and FOCUS in text
    assert "관양동 1023-4" in text


def test_with_focus_without_facts_is_unchanged():
    """사실이 없으면 예전 그대로 — 원문 (+ 초점 한 줄)."""
    assert loop.with_focus(UTTERANCE, {"focus": FOCUS}).endswith(FOCUS)
    assert loop.with_focus(UTTERANCE, None) == UTTERANCE


async def test_loop_run_query_takes_context_and_appends_the_focus_line():
    assert "context" in inspect.signature(loop.run_query).parameters
    model = FakeModel()
    await loop.run_query(UTTERANCE, mcp=FakeMcp(), model=model, context={"focus": FOCUS})
    user = model.seen[0][-1]
    assert user["role"] == "user"
    assert user["content"] == f"{UTTERANCE}\n[오케스트레이터 초점] {FOCUS}"


@pytest.mark.parametrize("context", [None, {}, {"focus": ""}, {"focus": UTTERANCE}, "문자열"])
async def test_loop_run_query_without_a_usable_focus_sends_the_utterance_alone(context):
    """초점이 없거나 발화와 같으면 붙이지 않는다 — 같은 문장을 두 번 읽히지 않는다."""
    model = FakeModel()
    await loop.run_query(UTTERANCE, mcp=FakeMcp(), model=model, context=context)
    assert model.seen[0][-1]["content"] == UTTERANCE
