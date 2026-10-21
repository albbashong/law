"""태스크 저장소 — 수명주기·전이 검증·contextId 색인·정리.

**상태를 바꾸는 코드는 `transition` 하나뿐이다.** 이게 이 파일의 설계 전부다.
전이가 코드 곳곳에 흩어지면 규격 준수를 테스트로 지킬 수 없고, 반드시 어딘가에서
규칙이 깨진다.

태스크는 프로세스 메모리에 둔다. 재시작하면 진행 중이던 것을 잃는데, 그때
`TaskNotFoundError` 404 를 정직하게 내는 것이 규격을 지키는 것이다 — 상태를
지어내는 것보다 낫다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

from .errors import TaskNotCancelableError, TaskNotFoundError, UnsupportedOperationError
from .types import (
    TERMINAL_STATES,
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
    new_id,
    now_iso,
)

#: 합법 전이표. 여기 없는 전이는 예외다.
#: 중단(INPUT_REQUIRED/AUTH_REQUIRED)에서 WORKING 으로 돌아오는 것이 되묻기 재개다.
_ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.SUBMITTED: frozenset({
        TaskState.WORKING, TaskState.REJECTED, TaskState.CANCELED, TaskState.FAILED,
    }),
    TaskState.WORKING: frozenset({
        TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED,
        TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED,
        # 거절은 접수 때만 나는 것이 아니다. SPEC 5장 흐름도가 **처리 분기**에
        # "내 일이 아님 → REJECTED" 를 두는데, 그 처리는 WORKING 에서 돈다 —
        # 2.5절 그림이 SUBMITTED 에서만 화살표를 그린 것은 그림의 누락이다.
        # 이 전이를 막으면 실행기가 거절을 COMPLETED 로 위장하거나 예외로 죽는다.
        TaskState.REJECTED,
    }),
    TaskState.INPUT_REQUIRED: frozenset({
        TaskState.WORKING, TaskState.CANCELED, TaskState.FAILED,
    }),
    TaskState.AUTH_REQUIRED: frozenset({
        TaskState.WORKING, TaskState.CANCELED, TaskState.FAILED,
    }),
}


class TaskRecord:
    """태스크 하나와 그에 딸린 것들.

    `runner` 는 실행 중인 asyncio 태스크다. 취소가 **실제로 멈추려면** 이 핸들이
    있어야 한다 — 상태만 CANCELED 로 바꾸고 일은 계속 도는 것은 거짓말이다.
    """

    __slots__ = ("task", "runner", "settled_at", "seen_messages")

    def __init__(self, task: Task) -> None:
        self.task = task
        self.runner: asyncio.Task | None = None
        self.settled_at: float | None = None
        self.seen_messages: set[str] = set()


class TaskStore:
    def __init__(self, *, ttl_sec: float = 3600.0, max_tasks: int = 1000) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._ttl_sec = ttl_sec
        self._max_tasks = max_tasks
        self._listeners: dict[str, list[asyncio.Queue]] = {}

    # ─────────────────────────────────────────── 조회

    def get(self, task_id: str) -> Task:
        rec = self._records.get(task_id)
        if rec is None:
            raise TaskNotFoundError(f"모르는 태스크입니다: {task_id}")
        return rec.task

    def record(self, task_id: str) -> TaskRecord:
        rec = self._records.get(task_id)
        if rec is None:
            raise TaskNotFoundError(f"모르는 태스크입니다: {task_id}")
        return rec

    def exists(self, task_id: str) -> bool:
        return task_id in self._records

    def list(self, *, context_id: str | None = None, state: TaskState | None = None,
             page_size: int = 50, offset: int = 0) -> tuple[list[Task], int]:
        """(페이지, 전체 개수). 최근 것이 앞에 온다."""
        items = [r.task for r in self._records.values()]
        if context_id:
            items = [t for t in items if t.context_id == context_id]
        if state:
            items = [t for t in items if t.status.state is state]
        items.reverse()
        return items[offset:offset + page_size], len(items)

    def __iter__(self) -> Iterator[Task]:
        return iter([r.task for r in self._records.values()])

    # ─────────────────────────────────────────── 생성·재개

    def create(self, *, context_id: str | None = None,
               message: Message | None = None) -> Task:
        self._prune()
        task = Task(
            id=new_id("t_"),
            context_id=context_id or new_id("c_"),
            status=TaskStatus(state=TaskState.SUBMITTED),
            artifacts=[], history=[],
        )
        rec = TaskRecord(task)
        self._records[task.id] = rec
        if message is not None:
            self.append_history(task.id, message)
        return task

    def resume_target(self, message: Message) -> Task:
        """`taskId` 를 실은 메시지가 이어붙을 태스크. 규격이 요구하는 거절을 여기서 한다."""
        task_id = message.task_id or ""
        task = self.get(task_id)                       # 없으면 TaskNotFoundError 404
        if task.status.state in TERMINAL_STATES:
            raise UnsupportedOperationError(
                f"종결된 태스크에는 메시지를 보낼 수 없습니다: {task.status.state.value}")
        if message.context_id and message.context_id != task.context_id:
            raise UnsupportedOperationError(
                "contextId 가 taskId 와 맞지 않습니다.")
        return task

    def is_duplicate(self, task_id: str, message_id: str) -> bool:
        """같은 `messageId` 를 이미 받았나 (규격 §3.3.1).

        클라이언트가 타임아웃으로 재전송하면 같은 메시지가 두 번 온다. 이걸 안 보면
        쓰기 에이전트에서 그대로 중복 실행이 된다.
        """
        rec = self._records.get(task_id)
        if rec is None:
            return False
        if message_id in rec.seen_messages:
            return True
        rec.seen_messages.add(message_id)
        return False

    def find_by_message_id(self, message_id: str) -> Task | None:
        for rec in self._records.values():
            if message_id in rec.seen_messages:
                return rec.task
        return None

    # ─────────────────────────────────────────── 전이 — 상태를 바꾸는 유일한 곳

    def transition(self, task_id: str, new_state: TaskState, *,
                   message: Message | str | None = None) -> Task:
        rec = self.record(task_id)
        task = rec.task
        current = task.status.state

        if new_state not in _ALLOWED.get(current, frozenset()):
            raise UnsupportedOperationError(
                f"허용되지 않는 전이입니다: {current.value} → {new_state.value}")

        if isinstance(message, str):
            message = Message.agent_text(message, task_id=task.id,
                                         context_id=task.context_id or "")
        task.status = TaskStatus(state=new_state, message=message, timestamp=now_iso())
        if message is not None:
            self.append_history(task_id, message)
        if new_state in TERMINAL_STATES:
            rec.settled_at = time.monotonic()
        self._publish(task_id, task)
        return task

    def cancel(self, task_id: str) -> Task:
        rec = self.record(task_id)
        if rec.task.status.state in TERMINAL_STATES:
            raise TaskNotCancelableError(
                f"이미 종결된 태스크입니다: {rec.task.status.state.value}")
        if rec.runner is not None and not rec.runner.done():
            rec.runner.cancel()              # 상태만 바꾸고 일은 계속 도는 것은 거짓말이다
        return self.transition(task_id, TaskState.CANCELED, message="취소되었습니다.")

    # ─────────────────────────────────────────── 내용

    def append_history(self, task_id: str, message: Message) -> None:
        task = self.get(task_id)
        if task.history is None:
            task.history = []
        task.history.append(message)

    def put_artifact(self, task_id: str, artifact: Artifact) -> None:
        """같은 이름이 이미 있으면 덮는다 — 폴링 클라이언트가 중복을 보지 않게."""
        task = self.get(task_id)
        if task.artifacts is None:
            task.artifacts = []
        for i, existing in enumerate(task.artifacts):
            if existing.artifact_id == artifact.artifact_id:
                task.artifacts[i] = artifact
                self._publish_artifact(task_id, artifact)
                return
        task.artifacts.append(artifact)
        self._publish_artifact(task_id, artifact)

    def put_metadata(self, task_id: str, key: str, value: Any) -> None:
        task = self.get(task_id)
        if task.metadata is None:
            task.metadata = {}
        task.metadata[key] = value

    # ─────────────────────────────────────────── 스트리밍 구독

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """이 태스크의 이벤트를 받을 큐. 동시 구독을 허용한다(규격 §3.5.2)."""
        if not self.exists(task_id):
            raise TaskNotFoundError(f"모르는 태스크입니다: {task_id}")
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(task_id)
        if not listeners:
            return
        if queue in listeners:
            listeners.remove(queue)
        if not listeners:
            self._listeners.pop(task_id, None)

    def _publish(self, task_id: str, task: Task) -> None:
        """모든 구독자가 같은 이벤트를 같은 순서로 받는다(규격 §3.5.2)."""
        for queue in list(self._listeners.get(task_id, ())):
            queue.put_nowait(("status", task))

    def _publish_artifact(self, task_id: str, artifact: Artifact) -> None:
        for queue in list(self._listeners.get(task_id, ())):
            queue.put_nowait(("artifact", artifact))

    # ─────────────────────────────────────────── 정리

    def _prune(self) -> None:
        """종결 후 TTL 이 지났거나 개수 상한을 넘으면 오래된 것부터 버린다.

        안 하면 무한히 쌓이고 `ListTasks` 도 같이 무거워진다.
        """
        now = time.monotonic()
        for task_id, rec in list(self._records.items()):
            if rec.settled_at is not None and now - rec.settled_at > self._ttl_sec:
                self._drop(task_id)
        while len(self._records) > self._max_tasks:
            settled = [(r.settled_at, tid) for tid, r in self._records.items()
                       if r.settled_at is not None]
            if not settled:
                break                        # 전부 진행 중이면 버리지 않는다
            settled.sort()
            self._drop(settled[0][1])

    def _drop(self, task_id: str) -> None:
        self._records.pop(task_id, None)
        self._listeners.pop(task_id, None)
