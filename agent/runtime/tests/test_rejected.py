"""내 일이 아니면 REJECTED 로 종결한다 (SPEC 5장 825행, 3.2절 391행).

`run_query` 가 `stopped="rejected"` 를 내면 실행기는 **완료도 실패도 아닌 거절**로
끝낸다. COMPLETED 로 끝내면 오케스트레이터가 성공으로 읽어 다른 경로를 안 찾고,
FAILED 로 끝내면 "고장났다"는 뜻이 되어 재시도를 부른다 — 둘 다 사실이 아니다.

거절도 종결이므로 `answer` 아티팩트(규격 4.2절 MUST)와 사유가 실린 `status.message`
가 있어야 하고, 스트림은 그 자리에서 닫혀야 한다(규격 §3.1.2 종결 넷).
"""

import asyncio
import json

import httpx

from a2a.server import build_app
from a2a.store import TaskStore
from a2a.types import TaskState, new_id
from executor import Executor

REASON = "위치검색 에이전트가 처리할 요청이 아닙니다: 공시지가 알려줘"

CARD_SPEC = {
    "name": "테스트 에이전트",
    "description": "위치를 찾는다. 공시지가는 이 에이전트가 아니다.",
    "version": "1.0.0",
    "skills": [{"id": "search", "name": "위치검색", "description": "위치를 찾는다.",
                "tags": ["위치"], "examples": ["안양시청 찾아줘"],
                "hints": {"readOnly": True, "idempotent": True}}],
}


async def rejecting_run_query(query, *, on_progress=None, prior=None, **kw):
    return {"answer": REASON, "tool_results": {}, "stopped": "rejected",
            "usage": {"tokens_in": 0, "tokens_out": 0}, "errors": [], "messages": []}


def shape_passthrough(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
            "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}


def _artifact_text(task, name):
    return next(a for a in task.artifacts if a.name == name).parts[0].text


async def test_rejected_stopped_ends_the_task_as_rejected_with_a_reason():
    store = TaskStore()
    ex = Executor(store, run_query=rejecting_run_query, shape_result=shape_passthrough)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)

    await ex.run(task.id, "공시지가 알려줘", resuming=False)

    settled = store.get(task.id)
    assert settled.status.state is TaskState.REJECTED
    assert settled.status.message.text() == REASON
    # answer 아티팩트는 종결 상태와 무관하게 MUST 다(규격 4.2절).
    assert _artifact_text(settled, "answer") == REASON
    assert ex.state_of(task.id).original == ""      # forget 했다 — 상태가 새지 않는다


async def test_rejected_without_answer_still_carries_a_message():
    """성형이 문장을 못 냈어도 사유 없는 거절은 없다."""
    store = TaskStore()

    async def silent(query, *, on_progress=None, prior=None, **kw):
        return {"answer": "", "tool_results": {}, "stopped": "rejected",
                "usage": {}, "errors": [], "messages": []}

    ex = Executor(store, run_query=silent, shape_result=shape_passthrough)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)

    await ex.run(task.id, "무엇이든", resuming=False)

    settled = store.get(task.id)
    assert settled.status.state is TaskState.REJECTED
    assert settled.status.message.text() == "이 에이전트가 처리할 요청이 아닙니다."


async def _client(store, ex):
    app = build_app(store=store, executor=ex, card_spec=CARD_SPEC, url="http://test")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test", headers={"A2A-Version": "1.0"})


def _body(text):
    return {"message": {"messageId": new_id("m_"), "role": "ROLE_USER",
                        "parts": [{"text": text}]}}


async def test_get_task_reports_rejected_over_the_wire():
    store = TaskStore()
    ex = Executor(store, run_query=rejecting_run_query, shape_result=shape_passthrough)
    async with await _client(store, ex) as client:
        sent = await client.post("/message:send", json=_body("공시지가 알려줘"))
        assert sent.status_code == 200
        task_id = sent.json()["task"]["id"]

        got = (await client.get(f"/tasks/{task_id}")).json()

    assert got["status"]["state"] == "TASK_STATE_REJECTED"
    assert REASON in json.dumps(got["status"]["message"], ensure_ascii=False)
    answer = next(a for a in got["artifacts"] if a["name"] == "answer")
    assert answer["parts"][0]["text"] == REASON


async def test_stream_closes_on_rejected_as_a_terminal_state():
    """규격 §3.1.2 — 닫는 조건은 종결 넷이고 REJECTED 가 그중 하나다."""
    store = TaskStore()
    ex = Executor(store, run_query=rejecting_run_query, shape_result=shape_passthrough)
    frames = []
    async with await _client(store, ex) as client:
        async with client.stream("POST", "/message:stream", timeout=10,
                                 json=_body("공시지가 알려줘")) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    (kind, payload), = json.loads(line[6:]).items()
                    frames.append((kind, payload))

    kind, payload = frames[-1]
    assert kind == "statusUpdate"
    assert payload["status"]["state"] == "TASK_STATE_REJECTED"
    assert any(k == "artifactUpdate" and p["artifact"]["name"] == "answer"
               for k, p in frames)


async def test_rejected_is_terminal_and_cannot_be_resumed():
    store = TaskStore()
    ex = Executor(store, run_query=rejecting_run_query, shape_result=shape_passthrough)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "공시지가 알려줘", resuming=False)

    for state in (TaskState.WORKING, TaskState.COMPLETED):
        try:
            store.transition(task.id, state)
        except Exception as exc:      # noqa: BLE001 — 어느 예외든 '되돌아갈 수 없다'면 된다
            assert "허용되지 않는 전이" in str(exc)
        else:
            raise AssertionError(f"종결에서 {state} 로 되돌아갔다")
    await asyncio.sleep(0)


async def test_resume_can_end_in_rejected_over_the_wire():
    """되묻고 나서야 내 일이 아님을 알 수도 있다 — INPUT_REQUIRED → WORKING → REJECTED.

    HTTP 수준으로 본다: 재개(taskId 를 실은 message:send)가 거절로 끝나면 상태는
    TASK_STATE_REJECTED 이고 사유가 실린다. 되묻기 상태로 남거나 COMPLETED 로 끝나면
    클라이언트가 사람에게 계속 묻거나 성공으로 읽는다 — 둘 다 사실이 아니다.
    """
    store = TaskStore()
    turns: list[str] = []

    async def ask_then_reject(query, *, on_progress=None, prior=None, **kw):
        turns.append(query)
        if len(turns) == 1:
            return {"answer": "", "tool_results": {}, "stopped": "ask",
                    "ask": {"question": "어느 위치인가요?", "candidates": []},
                    "usage": {}, "errors": [], "messages": []}
        return {"answer": REASON, "tool_results": {}, "stopped": "rejected",
                "usage": {}, "errors": [], "messages": []}

    ex = Executor(store, run_query=ask_then_reject, shape_result=shape_passthrough)
    async with await _client(store, ex) as client:
        sent = await client.post("/message:send", json=_body("공시지가"))
        first = sent.json()["task"]
        assert first["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

        body = _body("안양시청")
        body["message"]["taskId"] = first["id"]
        resumed = (await client.post("/message:send", json=body)).json()["task"]

        got = (await client.get(f"/tasks/{first['id']}")).json()

    assert resumed["status"]["state"] == "TASK_STATE_REJECTED"
    assert got["status"]["state"] == "TASK_STATE_REJECTED"
    assert REASON in json.dumps(got["status"]["message"], ensure_ascii=False)
    assert _artifact_text(store.get(first["id"]), "answer") == REASON
    assert len(turns) == 2 and turns[1].startswith("원래 요청: 공시지가")
