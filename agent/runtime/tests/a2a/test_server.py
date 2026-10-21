"""엔드포인트 계약 — 모델·MCP 없이."""

import asyncio

import pytest

from a2a.types import TaskState
from conftest import EXT, send_body, user_message

pytestmark = pytest.mark.asyncio


async def _settle(client, task_id, want=TaskState.COMPLETED, tries=50):
    for _ in range(tries):
        r = await client.get(f"/tasks/{task_id}")
        if r.json()["status"]["state"] == want.value:
            return r.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"{want.value} 에 닿지 못했다: {r.json()['status']['state']}")


class TestAgentCard:
    async def test_served_without_auth(self, make_client):
        client, _ = make_client()
        r = await client.get("/.well-known/agent-card.json")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/a2a+json")

    async def test_has_required_fields(self, make_client):
        client, _ = make_client()
        card = (await client.get("/.well-known/agent-card.json")).json()
        assert {"name", "description", "supportedInterfaces", "version", "capabilities",
                "defaultInputModes", "defaultOutputModes", "skills"} <= set(card)

    async def test_declaration_matches_routes(self, make_client):
        """§5.3 — 카드가 말한 것이 실제로 붙어 있어야 한다."""
        client, _ = make_client(streaming=False)
        card = (await client.get("/.well-known/agent-card.json")).json()
        assert card["capabilities"]["streaming"] is False
        r = await client.post("/message:stream", json=send_body("x"))
        assert r.status_code in (404, 405)

    async def test_streaming_route_exists_when_declared(self, make_client):
        client, _ = make_client(streaming=True)
        card = (await client.get("/.well-known/agent-card.json")).json()
        assert card["capabilities"]["streaming"] is True


class TestVersionNegotiation:
    """A2A 규격 3.6절 — 요청된 버전으로 처리하고 모르면 거절합니다."""

    async def test_supported_version_passes(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json=send_body("x"),
                              headers={"A2A-Version": "1.0"})
        assert r.status_code == 200

    async def test_patch_version_is_ignored(self, make_client):
        """Major.Minor 만 가린다 — 패치는 호환에 영향이 없습니다."""
        client, _ = make_client()
        r = await client.post("/message:send", json=send_body("x"),
                              headers={"A2A-Version": "1.0.7"})
        assert r.status_code == 200

    async def test_unknown_version_is_rejected(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json=send_body("x"),
                              headers={"A2A-Version": "99.0"})
        assert r.status_code == 400
        assert r.json()["title"] == "VersionNotSupportedError"

    async def test_missing_header_means_0_3(self, make_client):
        """빈 값은 0.3 으로 해석해야 한다 — 우리는 0.3 을 지원하지 않으므로 거절합니다."""
        client, _ = make_client()
        r = await client.post("/message:send", json=send_body("x"),
                              headers={"A2A-Version": ""})
        assert r.status_code == 400

    async def test_query_parameter_also_works(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send?A2A-Version=1.0", json=send_body("x"),
                              headers={"A2A-Version": ""})
        assert r.status_code == 200

    async def test_card_is_readable_without_version(self, make_client):
        """카드는 협상 전에 읽는 것이라 버전을 안 봅니다."""
        client, _ = make_client()
        r = await client.get("/.well-known/agent-card.json",
                             headers={"A2A-Version": ""})
        assert r.status_code == 200


class TestSendMessage:
    async def test_creates_task(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json=send_body("재고 있어?"))
        assert r.status_code == 200
        task = r.json()["task"]
        assert task["id"] and task["contextId"]

    async def test_return_immediately_does_not_wait(self, make_client):
        client, _ = make_client(delay=0.2)
        r = await client.post("/message:send", json=send_body("x"))
        assert r.json()["task"]["status"]["state"] == "TASK_STATE_WORKING"

    async def test_blocking_waits_for_terminal(self, make_client):
        """규격 §3.5 — returnImmediately 가 없으면 끝날 때까지 막는다."""
        client, _ = make_client(delay=0.05)
        r = await client.post("/message:send", json=send_body("x", immediate=False))
        assert r.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

    async def test_blocking_returns_on_interrupt(self, make_client):
        client, _ = make_client("clarify")
        r = await client.post("/message:send", json=send_body("x", immediate=False))
        assert r.json()["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    async def test_client_context_id_is_preserved(self, make_client):
        client, _ = make_client()
        body = send_body("x")
        body["message"]["contextId"] = "c-given"
        response = await client.post("/message:send", json=body)
        assert response.json()["task"]["contextId"] == "c-given"

    async def test_duplicate_message_id_does_not_create_a_second_task(self, make_client):
        """규격 §3.3.1 — 재전송이 중복 실행이 되면 안 된다."""
        client, ex = make_client()
        body = send_body("주문", message_id="m-fixed")
        first = (await client.post("/message:send", json=body)).json()["task"]["id"]
        await _settle(client, first)
        second = (await client.post("/message:send", json=body)).json()["task"]["id"]
        assert first == second
        assert len(ex.calls) == 1, "같은 메시지로 두 번 실행됐다"


class TestGetTask:
    async def test_unknown_is_404_problem_json(self, make_client):
        client, _ = make_client()
        r = await client.get("/tasks/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["title"] == "TaskNotFoundError"

    async def test_artifacts_and_usage_land(self, make_client):
        client, _ = make_client()
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        task = await _settle(client, tid)
        assert [a["name"] for a in task["artifacts"]] == ["answer"]
        assert task["metadata"][f"{EXT}/usage/v1"]["tokensIn"] == 10

    async def test_history_length_trims(self, make_client):
        client, _ = make_client()
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        await _settle(client, tid)
        r = await client.get(f"/tasks/{tid}?historyLength=1")
        assert len(r.json()["history"]) == 1


class TestTerminalRejections:
    async def test_message_to_terminal_task_is_400(self, make_client):
        client, _ = make_client()
        task = (await client.post("/message:send", json=send_body("x"))).json()["task"]
        await _settle(client, task["id"])
        r = await client.post("/message:send", json={
            "message": user_message("또", taskId=task["id"], contextId=task["contextId"])})
        assert r.status_code == 400
        assert r.json()["title"] == "UnsupportedOperationError"

    async def test_mismatched_context_is_400(self, make_client):
        client, _ = make_client("clarify")
        task = (await client.post("/message:send", json=send_body("x"))).json()["task"]
        await _settle(client, task["id"], TaskState.INPUT_REQUIRED)
        r = await client.post("/message:send", json={
            "message": user_message("답", taskId=task["id"], contextId="c-다름")})
        assert r.status_code == 400

    async def test_unknown_task_id_in_message_is_404(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json={
            "message": user_message("답", taskId="nope")})
        assert r.status_code == 404


class TestCancel:
    async def test_cancels_running(self, make_client):
        client, ex = make_client("hang")
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        await ex.started.wait()
        r = await client.post(f"/tasks/{tid}:cancel")
        assert r.json()["status"]["state"] == "TASK_STATE_CANCELED"

    async def test_terminal_is_not_cancelable(self, make_client):
        client, _ = make_client()
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        await _settle(client, tid)
        r = await client.post(f"/tasks/{tid}:cancel")
        assert r.status_code == 400
        assert r.json()["title"] == "TaskNotCancelableError"


class TestListTasks:
    async def test_lists_and_filters(self, make_client):
        client, _ = make_client()
        body = send_body("x")
        body["message"]["contextId"] = "c-a"
        await client.post("/message:send", json=body)
        await client.post("/message:send", json=send_body("y"))
        r = await client.get("/tasks?contextId=c-a")
        assert r.json()["totalSize"] == 1

    async def test_page_size_out_of_range_is_400(self, make_client):
        client, _ = make_client()
        assert (await client.get("/tasks?pageSize=150")).status_code == 400

    async def test_unknown_state_is_400(self, make_client):
        client, _ = make_client()
        assert (await client.get("/tasks?status=TASK_STATE_RUNNING")).status_code == 400


class TestPushNotifications:
    async def test_crud(self, make_client):
        client, _ = make_client()
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        cfg = (await client.post(f"/tasks/{tid}/pushNotificationConfigs",
                                 json={"url": "https://hook", "token": "t"})).json()
        assert cfg["url"] == "https://hook"
        assert (await client.get(f"/tasks/{tid}/pushNotificationConfigs")).json()["configs"]
        assert (await client.get(
            f"/tasks/{tid}/pushNotificationConfigs/{cfg['id']}")).status_code == 200
        assert (await client.delete(
            f"/tasks/{tid}/pushNotificationConfigs/{cfg['id']}")).status_code == 200
        assert (await client.get(
            f"/tasks/{tid}/pushNotificationConfigs/{cfg['id']}")).status_code == 404

    async def test_absent_when_not_declared(self, make_client):
        """§2.3.2 — 선언하지 않았으면 라우트도 없어야 한다."""
        client, _ = make_client(push_notifications=False)
        card = (await client.get("/.well-known/agent-card.json")).json()
        assert card["capabilities"]["pushNotifications"] is False
        tid = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        r = await client.post(f"/tasks/{tid}/pushNotificationConfigs",
                              json={"url": "https://hook"})
        assert r.status_code in (404, 405)


class TestMalformedRequests:
    """규격 §5.1 Validation Errors — 본문이 형식에 안 맞으면 400 problem+json 이어야 한다.

    검증 예외를 안 잡으면 FastAPI 기본 처리로 새어 plain text 500 이 된다. 클라이언트는
    자기 잘못인지 서버 고장인지 모른다(SPEC 2.7 "오류는 problem+json MUST" 위반).
    """

    async def test_missing_message_id_is_400_problem_json(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json={
            "message": {"role": "ROLE_USER", "parts": [{"text": "x"}]}})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["title"] == "InvalidParamsError"
        assert "messageId" in body["detail"]

    async def test_invalid_json_is_400_problem_json(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", content=b"{not json",
                              headers={"Content-Type": "application/json"})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["title"] == "JSONParseError"

    async def test_non_object_body_is_400(self, make_client):
        client, _ = make_client()
        r = await client.post("/message:send", json=[1, 2])
        assert r.status_code == 400
        assert r.json()["title"] == "InvalidParamsError"

    async def test_stream_missing_message_id_is_400(self, make_client):
        client, _ = make_client(streaming=True)
        r = await client.post("/message:stream", json={
            "message": {"role": "ROLE_USER", "parts": [{"text": "x"}]}})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/problem+json")

    async def test_push_config_without_url_is_400(self, make_client):
        client, _ = make_client(push_notifications=True)
        task_id = (await client.post("/message:send", json=send_body("x"))).json()["task"]["id"]
        r = await client.post(f"/tasks/{task_id}/pushNotificationConfigs", json={"id": "c1"})
        assert r.status_code == 400
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["title"] == "InvalidParamsError"
        assert "url" in body["detail"]
