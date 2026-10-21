"""SSE — 규격 §3.5.2·§11.7.

이벤트 순서와 종료 조건이 규범이다. 순서가 뒤섞이거나 스트림이 안 끝나면 받는 쪽이
영원히 기다린다.
"""

import asyncio
import json

import pytest

from a2a.types import TaskState
from conftest import send_body

pytestmark = pytest.mark.asyncio


async def collect(client, method, path, **kw):
    """SSE 프레임을 파싱해 [(oneof키, payload)] 로 돌려준다."""
    frames = []
    async with client.stream(method, path, timeout=10, **kw) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                (kind, payload), = json.loads(line[6:]).items()
                frames.append((kind, payload))
    return frames


class TestMessageStream:
    async def test_starts_with_task_object(self, make_client):
        """규격 §3.1.2 — 스트림은 Task 로 시작한다."""
        client, _ = make_client()
        frames = await collect(client, "POST", "/message:stream", json=send_body("x"))
        assert frames[0][0] == "task"
        assert frames[0][1]["id"]

    async def test_ends_with_terminal_status(self, make_client):
        client, _ = make_client()
        frames = await collect(client, "POST", "/message:stream", json=send_body("x"))
        kind, payload = frames[-1]
        assert kind == "statusUpdate"
        assert payload["status"]["state"] == "TASK_STATE_COMPLETED"

    async def test_carries_artifact_updates(self, make_client):
        client, _ = make_client()
        frames = await collect(client, "POST", "/message:stream", json=send_body("x"))
        arts = [p for k, p in frames if k == "artifactUpdate"]
        assert arts and arts[0]["artifact"]["name"] == "answer"
        assert arts[0]["taskId"] and arts[0]["contextId"]

    async def test_stays_open_at_interrupt(self, make_client):
        """규격 §3.1.2 — 닫는 조건은 **종결 넷뿐**이다.

        INPUT_REQUIRED 는 목록에 없다. 되묻기 왕복이 같은 스트림에서 이어지도록
        열어 둔다. 무응답이면 시한으로 닫힌다.
        """
        client, _ = make_client("clarify", stream_idle_sec=0.15)
        frames = await collect(client, "POST", "/message:stream", json=send_body("x"))
        assert frames[-1][1]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert frames[0][0] == "task"

    async def test_idle_timeout_closes_the_stream(self, make_client):
        """안 두면 아무도 답하지 않는 스트림이 영원히 열려 있다."""
        import time
        client, _ = make_client("clarify", stream_idle_sec=0.1)
        started = time.monotonic()
        await collect(client, "POST", "/message:stream", json=send_body("x"))
        assert time.monotonic() - started < 5

    async def test_frame_keys_are_the_oneof_names(self, make_client):
        client, _ = make_client()
        frames = await collect(client, "POST", "/message:stream", json=send_body("x"))
        assert set(k for k, _ in frames) <= {"task", "message", "statusUpdate",
                                             "artifactUpdate"}


class TestSubscribe:
    async def test_replays_current_task_first(self, make_client):
        client, ex = make_client("hang")
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        await ex.started.wait()

        async def watch():
            return await collect(client, "POST", f"/tasks/{tid}:subscribe")

        watcher = asyncio.create_task(watch())
        await asyncio.sleep(0.05)
        await client.post(f"/tasks/{tid}:cancel")
        frames = await watcher
        assert frames[0][0] == "task"
        assert frames[-1][1]["status"]["state"] == "TASK_STATE_CANCELED"

    async def test_terminal_task_cannot_be_subscribed(self, make_client):
        client, _ = make_client()
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        for _ in range(50):
            if (await client.get(f"/tasks/{tid}")).json()["status"]["state"] == \
                    TaskState.COMPLETED.value:
                break
            await asyncio.sleep(0.01)
        r = await client.post(f"/tasks/{tid}:subscribe")
        assert r.status_code == 400
        assert r.json()["title"] == "UnsupportedOperationError"

    async def test_unknown_task_is_404(self, make_client):
        client, _ = make_client()
        assert (await client.post("/tasks/nope:subscribe")).status_code == 404

    async def test_concurrent_streams_see_the_same_events(self, make_client):
        """규격 §3.5.2 — 모든 스트림이 같은 이벤트를 같은 순서로 받는다."""
        client, ex = make_client("hang")
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        await ex.started.wait()
        a = asyncio.create_task(collect(client, "POST", f"/tasks/{tid}:subscribe"))
        b = asyncio.create_task(collect(client, "POST", f"/tasks/{tid}:subscribe"))
        await asyncio.sleep(0.05)
        await client.post(f"/tasks/{tid}:cancel")
        fa, fb = await a, await b
        assert [k for k, _ in fa] == [k for k, _ in fb]
