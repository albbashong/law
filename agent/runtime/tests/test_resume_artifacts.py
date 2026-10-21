"""재개 사이에 낡은 candidates/errors 아티팩트가 남지 않는다 (F1, 규격 4.2절).

`_publish`/`run` 이 이전 턴에 실었던 아티팩트를 새 턴이 비웠을 때도 덮어써야 한다 —
안 그러면 확정된 뒤에도 지난 턴의 후보·오류가 폴링 클라이언트에 그대로 보인다.
"""

import asyncio

from a2a.store import TaskStore
from a2a.types import TaskState
from executor import Executor

CANDS = [{"pnu": "1", "label": "가"}, {"pnu": "2", "label": "나"}]
ERRORS = [{"tool": "parcel_spatial_survey", "kind": "tool_error", "message": "err",
          "recoverable": True, "attempts": 1}]


async def _wait(store, tid, state, tries=100):
    for _ in range(tries):
        if store.get(tid).status.state is state:
            return store.get(tid)
        await asyncio.sleep(0.005)
    raise AssertionError(f"{state} 에 못 닿았다: {store.get(tid).status.state}")


def _artifact_data(task, name):
    return next(a for a in task.artifacts if a.name == name).parts[0].data


def _artifact_text(task, name):
    return next(a for a in task.artifacts if a.name == name).parts[0].text


async def test_stale_candidates_and_errors_are_cleared_on_resume():
    store = TaskStore()
    calls: list[str] = []

    async def fake_run_query(query, *, on_progress=None, prior=None, **kw):
        calls.append(query)
        if len(calls) == 1:
            return {"answer": "", "tool_results": {}, "stopped": "answer",
                    "messages": [], "usage": {}, "errors": ERRORS}
        return {"answer": "확정했습니다.", "tool_results": {}, "stopped": "answer",
                "messages": [], "usage": {}, "errors": []}

    def fake_shape(answer, tool_results, usage=None):
        if len(calls) == 1:
            return {"message": "후보가 있습니다.", "status": "clarify",
                    "candidates": CANDS, "resolve": {}, "actions": [],
                    "tool_results": dict(tool_results), "usage": dict(usage or {})}
        return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
                "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}

    ex = Executor(store, run_query=fake_run_query, shape_result=fake_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "질의", resuming=False)
    asked = await _wait(store, task.id, TaskState.INPUT_REQUIRED)
    assert _artifact_data(asked, "candidates") == CANDS
    assert _artifact_data(asked, "errors") == ERRORS

    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "1번", resuming=True)
    done = await _wait(store, task.id, TaskState.COMPLETED)
    assert _artifact_data(done, "candidates") == []
    assert _artifact_data(done, "errors") == []
    assert done.status.message.text() == "확정했습니다."


async def test_facts_artifact_is_published_and_cleared_when_empty():
    """도구가 밝힌 사실을 `facts` 아티팩트로 올린다 — 코어가 그것을 사실 레지스터에 앉힌다(결정 6).

    다음 턴이 사실을 안 내면 빈 값으로 덮는다(낡은 후보를 지우는 F1 과 같은 규칙) — 안 그러면
    지난 턴의 대상이 사실로 남아 다음 턴이 엉뚱한 필지를 이어 쓴다.
    """
    store = TaskStore()
    calls: list[str] = []
    facts = {"pnu": "P1", "address": "관양동 1023-4"}

    async def fake_run_query(query, *, on_progress=None, prior=None, **kw):
        calls.append(query)
        return {"answer": "답", "tool_results": {}, "stopped": "answer",
                "messages": [], "usage": {}, "errors": []}

    def fake_shape(answer, tool_results, usage=None):
        # 첫 턴은 되묻기로 멈춘다(종결이 아니라야 같은 태스크로 재개할 수 있다).
        if len(calls) == 1:
            return {"message": "후보가 있습니다.", "status": "clarify", "candidates": CANDS,
                    "resolve": {}, "actions": [], "facts": facts,
                    "tool_results": dict(tool_results), "usage": dict(usage or {})}
        return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
                "actions": [], "facts": {},
                "tool_results": dict(tool_results), "usage": dict(usage or {})}

    ex = Executor(store, run_query=fake_run_query, shape_result=fake_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "관양동 1023-4 찾아줘", resuming=False)
    asked = await _wait(store, task.id, TaskState.INPUT_REQUIRED)
    assert _artifact_data(asked, "facts") == facts

    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "1번", resuming=True)
    done = await _wait(store, task.id, TaskState.COMPLETED)
    assert _artifact_data(done, "facts") == {}


async def test_ask_user_publishes_answer_artifact_and_clears_stale_candidates():
    store = TaskStore()
    calls: list[str] = []

    async def fake_run_query(query, *, on_progress=None, prior=None, **kw):
        calls.append(query)
        if len(calls) == 1:
            return {"answer": "", "tool_results": {}, "stopped": "answer",
                    "messages": [], "usage": {}, "errors": []}
        return {"answer": "", "ask": {"question": "어느 것입니까?", "candidates": []},
                "tool_results": {}, "stopped": "ask", "messages": [], "usage": {}}

    def fake_shape(answer, tool_results, usage=None):
        return {"message": "후보가 있습니다.", "status": "clarify",
                "candidates": CANDS, "resolve": None, "actions": [],
                "tool_results": dict(tool_results), "usage": dict(usage or {})}

    ex = Executor(store, run_query=fake_run_query, shape_result=fake_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "질의", resuming=False)
    asked = await _wait(store, task.id, TaskState.INPUT_REQUIRED)
    assert _artifact_data(asked, "candidates") == CANDS

    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "1번", resuming=True)
    asked2 = await _wait(store, task.id, TaskState.INPUT_REQUIRED)
    assert _artifact_text(asked2, "answer") == "어느 것입니까?"
    assert _artifact_data(asked2, "candidates") == []
