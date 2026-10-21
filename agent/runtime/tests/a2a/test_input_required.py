"""되묻기 왕복과 재개 상태 — SPEC §2.7.

이 규격에서 가장 비싼 부분이고, 실측에서 통과율 0% 를 만든 자리다. 형식만 맞고
루프가 안 닫히는 것을 잡으려면 **답을 실제로 보내 봐야** 한다.
"""

import asyncio

import pytest

from a2a.store import TaskStore
from a2a.types import TaskState
from conftest import send_body, user_message
from executor import Executor

pytestmark = pytest.mark.asyncio


async def _settle(client, tid, want, tries=50):
    for _ in range(tries):
        r = await client.get(f"/tasks/{tid}")
        if r.json()["status"]["state"] == want.value:
            return r.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"{want.value} 에 못 닿았다: {r.json()['status']['state']}")


class TestRoundTrip:
    async def test_asks_with_question_and_candidates(self, make_client):
        client, _ = make_client("clarify")
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        task = await _settle(client, tid, TaskState.INPUT_REQUIRED)
        msg = task["status"]["message"]
        assert msg["role"] == "ROLE_AGENT"
        assert msg["taskId"] == tid and msg["contextId"] == task["contextId"]
        assert msg["parts"][0]["text"]
        assert any(a["name"] == "candidates" for a in task["artifacts"])

    async def test_candidates_are_distinguishable(self, make_client):
        """같은 표시가 둘이면 사용자가 골라도 구별되지 않아 루프가 안 닫힌다."""
        client, _ = make_client("clarify")
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        task = await _settle(client, tid, TaskState.INPUT_REQUIRED)
        data = next(a for a in task["artifacts"] if a["name"] == "candidates")["parts"][0]["data"]
        assert len({d["label"] for d in data}) == len(data)

    async def test_resume_continues_the_same_task(self, make_client):
        client, ex = make_client("clarify")
        task = (await client.post("/message:send", json=send_body("x"))).json()["task"]
        await _settle(client, task["id"], TaskState.INPUT_REQUIRED)
        r = await client.post("/message:send", json={
            "message": user_message("첫째", taskId=task["id"], contextId=task["contextId"]),
            "configuration": {"returnImmediately": True}})
        assert r.json()["task"]["id"] == task["id"], "재개가 새 태스크를 만들었다"
        await _settle(client, task["id"], TaskState.COMPLETED)

    async def test_executor_is_told_it_is_resuming(self, make_client):
        client, ex = make_client("clarify")
        task = (await client.post("/message:send", json=send_body("x"))).json()["task"]
        await _settle(client, task["id"], TaskState.INPUT_REQUIRED)
        await client.post("/message:send", json={
            "message": user_message("첫째", taskId=task["id"]),
            "configuration": {"returnImmediately": True}})
        await _settle(client, task["id"], TaskState.COMPLETED)
        assert [c[2] for c in ex.calls] == [False, True]

    async def test_history_records_both_sides(self, make_client):
        """관찰자가 무슨 대화가 오갔는지 재구성할 수 있어야 한다(§2.7)."""
        client, _ = make_client("clarify")
        task = (await client.post("/message:send", json=send_body("x"))).json()["task"]
        await _settle(client, task["id"], TaskState.INPUT_REQUIRED)
        await client.post("/message:send", json={
            "message": user_message("첫째", taskId=task["id"]),
            "configuration": {"returnImmediately": True}})
        final = await _settle(client, task["id"], TaskState.COMPLETED)
        roles = [m["role"] for m in final["history"]]
        assert roles.count("ROLE_USER") >= 2 and "ROLE_AGENT" in roles


class TestExecutorResume:
    """Executor 가 실제로 상태를 들고 있는지 — 가짜 run_query 로."""

    def _executor(self, store, script):
        """script: 호출 순서대로 돌려줄 결과 목록."""
        seen = []

        async def fake_run_query(query, *, on_progress=None, prior=None, **kw):
            seen.append({"query": query, "prior": prior})
            return script[len(seen) - 1]

        def fake_shape(answer, tool_results, usage=None):
            return {"message": answer, "status": tool_results.get("_status", "ok"),
                    "candidates": tool_results.get("_candidates", []),
                    "resolve": None, "actions": [], "tool_results": tool_results,
                    "usage": usage or {"tokens_in": 0, "tokens_out": 0}}

        ex = Executor(store, run_query=fake_run_query, shape_result=fake_shape)
        return ex, seen

    async def test_keeps_the_three_musts_after_asking(self):
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)
        ex, seen = self._executor(store, [
            {"answer": "어느 것입니까?", "usage": {}, "messages": [{"role": "user"}],
             "tool_results": {"_status": "clarify",
                              "_candidates": [{"sku": "A-1", "label": "첫째"}]}},
        ])
        await ex.run(task.id, "머그컵 재고", resuming=False)
        state = ex.state_of(task.id)
        assert state.original == "머그컵 재고"
        assert state.asked == "어느 것입니까?"
        assert state.candidates == [{"sku": "A-1", "label": "첫째"}]
        assert store.get(task.id).status.state is TaskState.INPUT_REQUIRED

    async def test_resume_query_is_wrapped_not_raw(self):
        """답변만 넘기면 모델이 새 질의로 읽는다 — 그게 0% 의 원인이었다."""
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)
        ex, seen = self._executor(store, [
            {"answer": "어느 것?", "usage": {}, "messages": [],
             "tool_results": {"_status": "clarify", "_candidates": [{"sku": "A-1"}]}},
            {"answer": "끝났습니다", "usage": {}, "messages": [], "tool_results": {}},
        ])
        await ex.run(task.id, "머그컵 재고", resuming=False)
        store.transition(task.id, TaskState.WORKING)
        await ex.run(task.id, "첫째", resuming=True)
        second = seen[1]["query"]
        assert "머그컵 재고" in second and "어느 것?" in second and "A-1" in second
        assert second != "첫째", "답변을 그대로 넘겼다"

    async def test_prior_carries_tool_results_forward(self):
        """같은 조회를 다시 하지 않게(§2.7 SHOULD)."""
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)
        ex, seen = self._executor(store, [
            {"answer": "어느 것?", "usage": {}, "messages": [{"role": "assistant"}],
             "tool_results": {"_status": "clarify", "_candidates": [{"s": 1}],
                              "lookup": {"hit": True}}},
            {"answer": "끝", "usage": {}, "messages": [], "tool_results": {}},
        ])
        await ex.run(task.id, "질의", resuming=False)
        store.transition(task.id, TaskState.WORKING)
        await ex.run(task.id, "답", resuming=True)
        assert seen[1]["prior"]["tool_results"]["lookup"] == {"hit": True}
        assert seen[1]["prior"]["messages"] == [{"role": "assistant"}]

    async def test_resumes_even_without_candidates(self):
        """에이전트는 후보 없이 물을 수도 있다 — 그때도 재개가 되어야 한다.

        후보를 재개 조건으로 걸면 답변이 새 질의로 읽혀 같은 질문이 되돌아온다.
        실측에서 이 경로가 3/3 으로 깨졌다.
        """
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)
        ex, seen = self._executor(store, [
            {"answer": "주소를 알려주세요", "usage": {}, "messages": [],
             "tool_results": {"_status": "clarify"}},          # 후보 없음
            {"answer": "일반상업지역입니다", "usage": {}, "messages": [], "tool_results": {}},
        ])
        await ex.run(task.id, "규제 알려줘", resuming=False)
        assert store.get(task.id).status.state is TaskState.INPUT_REQUIRED
        assert ex.state_of(task.id).candidates == []
        store.transition(task.id, TaskState.WORKING)
        await ex.run(task.id, "세종대로 110", resuming=True)
        second = seen[1]["query"]
        assert second != "세종대로 110", "답변을 그대로 넘겼다 — 새 질의로 읽힌다"
        assert "규제 알려줘" in second and "주소를 알려주세요" in second
        assert store.get(task.id).status.state is TaskState.COMPLETED

    async def test_state_is_forgotten_when_settled(self):
        """내부 상태는 태스크와 수명을 같이한다 — 안 그러면 샌다."""
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)
        ex, _ = self._executor(store, [
            {"answer": "끝", "usage": {}, "messages": [], "tool_results": {}}])
        await ex.run(task.id, "질의", resuming=False)
        assert task.id not in ex._states

    async def test_failure_is_a_normal_settlement(self):
        store = TaskStore()
        task = store.create()
        store.transition(task.id, TaskState.WORKING)

        async def boom(query, **kw):
            raise RuntimeError("도구에 닿지 못했습니다")

        ex = Executor(store, run_query=boom, shape_result=lambda *a, **k: {})
        await ex.run(task.id, "질의", resuming=False)
        settled = store.get(task.id)
        assert settled.status.state is TaskState.FAILED
        assert "도구에 닿지 못했습니다" in settled.status.message.text()
