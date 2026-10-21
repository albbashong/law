"""서버 테스트의 공용 장치.

모델도 MCP 도 부르지 않는다. 매 테스트가 모델을 부르면 느리고 비싸서 아무도 안
돌리게 되고, 안 돌리는 테스트는 없는 것과 같다.
"""

import asyncio
from typing import Any

import httpx
import pytest

from a2a.extensions import USAGE
from a2a.server import build_app
from a2a.store import TaskStore
from a2a.types import Artifact, TaskState

EXT = USAGE.rsplit("/", 2)[0]  # 공표된 뿌리 — 설정이 아니다

CARD_SPEC = {
    "name": "테스트 에이전트",
    "description": "재고를 조회한다. 주문은 이 에이전트가 아니다.",
    "version": "1.0.0",
    "skills": [{"id": "lookup", "name": "조회", "description": "조회한다. 주문은 안 한다.",
                "tags": ["조회"], "examples": ["재고 있어?"],
                "hints": {"readOnly": True, "idempotent": True}}],
}


class FakeExecutor:
    """지시한 대로 끝내는 실행기. 무엇을 언제 하는지 테스트가 정한다."""

    def __init__(self, store: TaskStore, *, mode: str = "complete", delay: float = 0.0):
        self.store = store
        self.mode = mode
        self.delay = delay
        self.calls: list[tuple[str, str, bool, dict]] = []
        self.started = asyncio.Event()

    async def run(self, task_id: str, text: str, *, resuming: bool,
                  context: dict | None = None) -> None:
        self.calls.append((task_id, text, resuming, dict(context or {})))
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        store = self.store
        if self.mode == "hang":
            await asyncio.sleep(3600)
        if self.mode == "fail":
            store.transition(task_id, TaskState.FAILED, message="도구에 닿지 못했습니다.")
            return
        if self.mode == "clarify" and not resuming:
            store.put_artifact(task_id, Artifact.data(
                task_id, "candidates",
                [{"sku": "A-1", "label": "첫째"}, {"sku": "A-2", "label": "둘째"}]))
            store.transition(task_id, TaskState.INPUT_REQUIRED, message="어느 것입니까?")
            return
        store.put_artifact(task_id, Artifact.text(task_id, "answer", f"처리했습니다: {text}"))
        store.put_metadata(task_id, f"{EXT}/usage/v1", {"tokensIn": 10, "tokensOut": 3})
        store.transition(task_id, TaskState.COMPLETED, message=f"처리했습니다: {text}")


@pytest.fixture
def store() -> TaskStore:
    return TaskStore()


@pytest.fixture
def make_client(store):
    """(mode, **kw) → (httpx.AsyncClient, FakeExecutor)."""
    created: list[Any] = []

    def _make(mode: str = "complete", **kw):
        ex = FakeExecutor(store, mode=mode, delay=kw.pop("delay", 0.0))
        app = build_app(store=store, executor=ex, card_spec=CARD_SPEC,
                        url="http://test", **kw)
        # 클라이언트는 요청마다 버전을 보내야 한다(A2A 규격 3.6.1절).
        # 안 보내면 서버가 0.3 으로 해석해 거절한다 — 규격이 그렇게 정했다.
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"A2A-Version": "1.0"})
        created.append(client)
        return client, ex

    yield _make
    for c in created:
        asyncio.get_event_loop().run_until_complete(c.aclose()) if False else None


def user_message(text: str, **kw) -> dict:
    from a2a.types import new_id
    return {"messageId": kw.pop("message_id", new_id("m_")), "role": "ROLE_USER",
            "parts": [{"text": text}], **kw}


def send_body(text: str, *, immediate: bool = True, **kw) -> dict:
    body: dict[str, Any] = {"message": user_message(text, **kw)}
    if immediate:
        body["configuration"] = {"returnImmediately": True}
    return body
