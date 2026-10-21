"""TaskStore — 상태 기계와 수명주기.

상태를 바꾸는 코드가 `transition` 하나뿐이라 여기서 전이를 전수로 검사할 수 있다.
"""

import asyncio

import pytest

from a2a.errors import TaskNotCancelableError, TaskNotFoundError, UnsupportedOperationError
from a2a.store import TaskStore
from a2a.types import (
    TERMINAL_STATES,
    Artifact,
    Message,
    Part,
    Role,
    TaskState,
)


@pytest.fixture
def store() -> TaskStore:
    return TaskStore()


def user_msg(text="hi", **kw) -> Message:
    return Message(message_id=kw.pop("message_id", "m1"), role=Role.USER,
                   parts=[Part.of_text(text)], **kw)


class TestCreate:
    def test_starts_submitted(self, store):
        assert store.create().status.state is TaskState.SUBMITTED

    def test_generates_ids(self, store):
        task = store.create()
        assert task.id and task.context_id
        assert task.id != task.context_id

    def test_accepts_client_context_id(self, store):
        assert store.create(context_id="c-given").context_id == "c-given"

    def test_seeds_history_with_message(self, store):
        task = store.create(message=user_msg("첫 발화"))
        assert [m.text() for m in task.history] == ["첫 발화"]

    def test_unknown_task_raises_404(self, store):
        with pytest.raises(TaskNotFoundError):
            store.get("nope")


class TestTransitions:
    def test_happy_path(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.COMPLETED, message="끝")
        assert store.get(t.id).status.state is TaskState.COMPLETED

    @pytest.mark.parametrize("target", sorted(TERMINAL_STATES, key=lambda s: s.value))
    def test_terminal_states_are_final(self, store, target):
        t = store.create()
        if target is not TaskState.REJECTED:
            store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, target)
        with pytest.raises(UnsupportedOperationError):
            store.transition(t.id, TaskState.WORKING)

    def test_interrupted_can_resume_to_working(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.INPUT_REQUIRED, message="어느 것?")
        store.transition(t.id, TaskState.WORKING)
        assert store.get(t.id).status.state is TaskState.WORKING

    def test_submitted_cannot_jump_to_completed(self, store):
        """WORKING 을 거치지 않는 전이는 막는다."""
        t = store.create()
        with pytest.raises(UnsupportedOperationError):
            store.transition(t.id, TaskState.COMPLETED)

    def test_submitted_can_be_rejected(self, store):
        t = store.create()
        store.transition(t.id, TaskState.REJECTED, message="제 일이 아닙니다.")
        assert store.get(t.id).status.state is TaskState.REJECTED

    def test_transition_records_timestamp_and_message(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.INPUT_REQUIRED, message="질문")
        status = store.get(t.id).status
        assert status.timestamp and status.message.text() == "질문"
        assert status.message.role is Role.AGENT

    def test_status_message_lands_in_history(self, store):
        t = store.create(message=user_msg())
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.COMPLETED, message="답")
        assert [m.role for m in store.get(t.id).history] == [Role.USER, Role.AGENT]


class TestResumeTarget:
    def test_returns_the_task(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.INPUT_REQUIRED, message="?")
        assert store.resume_target(user_msg(task_id=t.id)).id == t.id

    def test_unknown_task_id_is_404(self, store):
        with pytest.raises(TaskNotFoundError):
            store.resume_target(user_msg(task_id="nope"))

    def test_terminal_task_is_rejected(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.COMPLETED)
        with pytest.raises(UnsupportedOperationError):
            store.resume_target(user_msg(task_id=t.id))

    def test_mismatched_context_is_rejected(self, store):
        """규격 §3.4.3 — contextId 와 taskId 가 어긋나면 MUST 거절."""
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        with pytest.raises(UnsupportedOperationError):
            store.resume_target(user_msg(task_id=t.id, context_id="c-다른것"))

    def test_matching_context_is_accepted(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        assert store.resume_target(user_msg(task_id=t.id, context_id=t.context_id)).id == t.id

    def test_context_id_omitted_is_accepted(self, store):
        """taskId 만 줘도 된다 — 에이전트가 contextId 를 추론한다."""
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        assert store.resume_target(user_msg(task_id=t.id)).id == t.id


class TestDuplicateMessages:
    def test_same_message_id_is_duplicate(self, store):
        """규격 §3.3.1 — 재전송으로 태스크가 둘 생기면 안 된다."""
        t = store.create()
        assert store.is_duplicate(t.id, "m-1") is False
        assert store.is_duplicate(t.id, "m-1") is True

    def test_different_ids_are_not(self, store):
        t = store.create()
        store.is_duplicate(t.id, "m-1")
        assert store.is_duplicate(t.id, "m-2") is False

    def test_find_by_message_id(self, store):
        t = store.create()
        store.is_duplicate(t.id, "m-1")
        assert store.find_by_message_id("m-1").id == t.id
        assert store.find_by_message_id("m-2") is None


class TestCancel:
    def test_cancels_running_task(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        assert store.cancel(t.id).status.state is TaskState.CANCELED

    def test_terminal_task_is_not_cancelable(self, store):
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        store.transition(t.id, TaskState.COMPLETED)
        with pytest.raises(TaskNotCancelableError):
            store.cancel(t.id)

    @pytest.mark.asyncio
    async def test_actually_stops_the_runner(self, store):
        """상태만 바꾸고 일은 계속 도는 것은 거짓말이다."""
        t = store.create()
        store.transition(t.id, TaskState.WORKING)
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(30)

        store.record(t.id).runner = asyncio.create_task(work())
        await started.wait()
        store.cancel(t.id)
        await asyncio.sleep(0)
        assert store.record(t.id).runner.cancelled() or store.record(t.id).runner.done()


class TestArtifacts:
    def test_put_and_read(self, store):
        t = store.create()
        store.put_artifact(t.id, Artifact.text(t.id, "answer", "결과"))
        assert [a.name for a in store.get(t.id).artifacts] == ["answer"]

    def test_same_id_replaces(self, store):
        """폴링 클라이언트가 같은 이름을 두 번 보면 안 된다."""
        t = store.create()
        store.put_artifact(t.id, Artifact.text(t.id, "answer", "처음"))
        store.put_artifact(t.id, Artifact.text(t.id, "answer", "나중"))
        artifacts = store.get(t.id).artifacts
        assert len(artifacts) == 1
        assert artifacts[0].parts[0].text == "나중"

    def test_metadata(self, store):
        t = store.create()
        store.put_metadata(t.id, "https://x/ext/usage/v1", {"tokensIn": 1})
        assert store.get(t.id).metadata["https://x/ext/usage/v1"]["tokensIn"] == 1


class TestListing:
    def test_filters_by_context(self, store):
        a = store.create(context_id="c1")
        store.create(context_id="c2")
        items, total = store.list(context_id="c1")
        assert [t.id for t in items] == [a.id] and total == 1

    def test_filters_by_state(self, store):
        a = store.create()
        store.create()
        store.transition(a.id, TaskState.WORKING)
        items, total = store.list(state=TaskState.WORKING)
        assert [t.id for t in items] == [a.id] and total == 1

    def test_paginates(self, store):
        for _ in range(5):
            store.create()
        items, total = store.list(page_size=2)
        assert len(items) == 2 and total == 5


class TestPruning:
    def test_settled_tasks_expire(self):
        store = TaskStore(ttl_sec=-1)          # 즉시 만료
        old = store.create()
        store.transition(old.id, TaskState.WORKING)
        store.transition(old.id, TaskState.COMPLETED)
        store.create()                          # _prune 을 부른다
        assert not store.exists(old.id)

    def test_running_tasks_are_never_dropped(self):
        store = TaskStore(ttl_sec=-1, max_tasks=1)
        running = store.create()
        store.transition(running.id, TaskState.WORKING)
        for _ in range(5):
            store.create()
        assert store.exists(running.id)

    def test_max_tasks_drops_oldest_settled(self):
        store = TaskStore(max_tasks=2)
        first = store.create()
        store.transition(first.id, TaskState.WORKING)
        store.transition(first.id, TaskState.COMPLETED)
        for _ in range(4):
            store.create()
        assert not store.exists(first.id)


class TestSubscription:
    @pytest.mark.asyncio
    async def test_all_subscribers_get_same_events_in_order(self, store):
        """규격 §3.5.2 — 동시 스트림은 같은 이벤트를 같은 순서로 받는다."""
        t = store.create()
        q1, q2 = store.subscribe(t.id), store.subscribe(t.id)
        store.transition(t.id, TaskState.WORKING)
        store.put_artifact(t.id, Artifact.text(t.id, "answer", "x"))
        store.transition(t.id, TaskState.COMPLETED)
        seen1 = [q1.get_nowait()[0] for _ in range(3)]
        seen2 = [q2.get_nowait()[0] for _ in range(3)]
        assert seen1 == seen2 == ["status", "artifact", "status"]

    @pytest.mark.asyncio
    async def test_unsubscribe_does_not_affect_others(self, store):
        t = store.create()
        q1, q2 = store.subscribe(t.id), store.subscribe(t.id)
        store.unsubscribe(t.id, q1)
        store.transition(t.id, TaskState.WORKING)
        assert q2.qsize() == 1 and q1.qsize() == 0

    def test_subscribe_to_unknown_task_is_404(self, store):
        with pytest.raises(TaskNotFoundError):
            store.subscribe("nope")
