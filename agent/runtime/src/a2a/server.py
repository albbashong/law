"""A2A v1.0 HTTP+JSON 바인딩 — 엔드포인트 10개 + 카드.

라우트와 카드를 **같은 자리에서** 만든다(SPEC §5.3). 카드가 `streaming: true` 라고
말하면 그 라우트가 실제로 붙어 있어야 하고, 그 둘이 갈라지지 않는 유일한 방법은
한 곳에서 선언하는 것이다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from .card import build_card, load_card
from .errors import (
    A2AError,
    InvalidParamsError,
    JSONParseError,
    PushNotificationNotSupportedError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from .store import TaskStore
from .types import (
    TERMINAL_STATES,
    ListTaskPushNotificationConfigsResponse,
    ListTasksResponse,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatusUpdateEvent,
    stream_frame,
)

A2A_JSON = "application/a2a+json"
PROBLEM_JSON = "application/problem+json"
CARD_ROUTE = "/.well-known/agent-card.json"

#: 이 서버가 말하는 프로토콜 버전. Major.Minor 만 가린다 — 패치는 호환에 영향이 없다.
SUPPORTED_VERSIONS = frozenset({"1.0"})
#: 규격이 정한 기본값. **빈 값은 0.3 으로 해석해야 한다**(A2A 규격 3.6.2절) —
#: 그래서 헤더를 안 보면 모든 요청을 구버전으로 처리하는 셈이 된다.
DEFAULT_VERSION = "0.3"


def _context_of(msg: Any) -> dict[str, Any]:
    """dict 인 data 파트를 하나로 합친다 — 뒤가 앞을 덮는다. 없으면 빈 사전.

    data 파트는 oneof 라 무엇이든 실릴 수 있다(문자열·목록도 규격 위반이 아니다). 사전이 아닌
    것은 합칠 자리가 없으므로 무시한다 — 발화(text)는 그것과 무관하게 그대로 남는다.
    """
    merged: dict[str, Any] = {}
    for part in msg.parts:
        if isinstance(part.data, dict):
            merged.update(part.data)
    return merged


def _json(payload: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, media_type=A2A_JSON)


def _problem(exc: A2AError) -> JSONResponse:
    return JSONResponse(exc.problem(), status_code=exc.status, media_type=PROBLEM_JSON)


def _validation_detail(errors: list[dict[str, Any]]) -> str:
    """pydantic 오류 목록 → "어느 필드가 왜" 한 줄(규격 §5.1 — 실패한 파라미터를 밝히라 SHOULD)."""
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "요청 본문이 형식에 맞지 않습니다."


def _validate[M: BaseModel](model: type[M], data: Any) -> M:
    """요청 본문을 모델로 — 안 맞으면 `InvalidParamsError`(400 problem+json).

    잡지 않으면 pydantic 예외가 FastAPI 기본 처리로 새어 plain text 500 이 된다.
    클라이언트는 자기 잘못인지 서버 고장인지 모른다. 규격 2.7 은 오류를 problem+json
    으로만 내라고 한다.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise InvalidParamsError(_validation_detail(exc.errors())) from exc


def build_app(
    *,
    store: TaskStore,
    executor: Any,
    card_path: Any = None,
    card_spec: dict[str, Any] | None = None,
    url: str,
    streaming: bool = True,
    push_notifications: bool = True,
    stream_idle_sec: float = 300.0,
    title: str = "a2a-agent",
) -> FastAPI:
    """카드와 라우트를 함께 만든다.

    `streaming`/`push_notifications` 는 **한 값이 둘 다** 정한다 — 카드의 선언과
    라우트의 존재. 그래서 갈라질 수 없다.
    """
    card = (build_card(card_spec, url=url,
                       streaming=streaming, push_notifications=push_notifications)
            if card_spec is not None else
            load_card(card_path, url=url,
                      streaming=streaming, push_notifications=push_notifications))
    push_configs: dict[str, dict[str, TaskPushNotificationConfig]] = {}

    app = FastAPI(title=title, docs_url=None, redoc_url=None)
    router = APIRouter()

    @app.exception_handler(A2AError)
    async def _on_a2a_error(_req: Request, exc: A2AError) -> JSONResponse:
        return _problem(exc)

    @app.exception_handler(RequestValidationError)
    async def _on_bad_body(_req: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI 가 본문을 못 읽은 경우(JSON 아님·객체 아님) — 기본 422 대신 400 problem+json."""
        errors = exc.errors()
        if any(err.get("type") == "json_invalid" for err in errors):
            # 위치(loc)는 바이트 오프셋이라 필드 이름이 아니다 — 메시지만 싣는다.
            return _problem(JSONParseError(
                "; ".join(str(err.get("msg")) for err in errors)))
        return _problem(InvalidParamsError(_validation_detail(errors)))

    @app.middleware("http")
    async def _version_gate(request: Request, call_next):
        """요청된 A2A 버전으로 처리하고, 모르면 거절한다(A2A 규격 3.6절).

        카드는 버전 협상 전에 읽는 것이라 통과시킨다 — 클라이언트가 무엇을
        말해야 할지 카드를 보고 정한다.
        """
        if request.url.path == CARD_ROUTE:
            return await call_next(request)
        raw = (request.headers.get("A2A-Version")
               or request.query_params.get("A2A-Version") or "")
        version = ".".join(raw.strip().split(".")[:2]) if raw.strip() else DEFAULT_VERSION
        if version not in SUPPORTED_VERSIONS:
            return _problem(VersionNotSupportedError(
                f"지원하지 않는 A2A 버전입니다: {raw or '(없음)'}. "
                f"지원: {', '.join(sorted(SUPPORTED_VERSIONS))}"))
        return await call_next(request)

    # ─────────────────────────────────────── 발견

    @router.get(CARD_ROUTE)
    async def agent_card() -> Response:
        return _json(card.wire())

    # ─────────────────────────────────────── 메시지

    async def _accept(body: dict[str, Any]) -> tuple[Task, str, bool, dict[str, Any]]:
        """요청을 받아 태스크를 정하고 WORKING 으로 올린다. (태스크, 발화, 재개인가, 맥락)

        `발화` 는 **text 파트만** 이어 붙인 것이고, `맥락` 은 dict 인 data 파트를 합친 것이다
        (뒤가 앞을 덮는다). 호출자(코어)가 자기 초점처럼 아는 것을 실어 보내는 표준 자리다 —
        발화를 고쳐 쓰는 대신 원문은 text 로 두고 덧붙일 것을 data 로 준다(A2A `Message.parts`).
        """
        req = _validate(SendMessageRequest, body)
        msg = req.message
        text = msg.text()
        context = _context_of(msg)

        if msg.task_id:
            task = store.resume_target(msg)          # 규격이 요구하는 거절 세 가지
            resuming = True
        else:
            # 같은 messageId 가 다시 오면 새 태스크를 만들지 않는다(규격 §3.3.1).
            # 클라이언트의 타임아웃 재전송이 중복 실행이 되지 않게 한다.
            if existing := store.find_by_message_id(msg.message_id):
                return existing, text, False, context
            task = store.create(context_id=msg.context_id, message=msg)
            resuming = False

        store.is_duplicate(task.id, msg.message_id)
        if resuming:
            store.append_history(task.id, msg)
            store.transition(task.id, TaskState.WORKING)
        else:
            store.transition(task.id, TaskState.WORKING)
        return task, text, resuming, context

    @router.post("/message:send")
    async def send_message(body: dict[str, Any]) -> Response:
        task, text, resuming, context = await _accept(body)
        if store.get(task.id).status.state in TERMINAL_STATES:
            return _json({"task": store.get(task.id).wire()})   # 중복 재전송

        immediate = bool((body.get("configuration") or {}).get("returnImmediately"))
        runner = asyncio.create_task(
            executor.run(task.id, text, resuming=resuming, context=context))
        store.record(task.id).runner = runner

        if immediate:
            return _json({"task": store.get(task.id).wire()})
        # 블로킹 모드 — 종결·중단까지 기다린다(규격 §3.5). 둘 다 MUST 다.
        try:
            await runner
        except asyncio.CancelledError:
            pass
        return _json({"task": store.get(task.id).wire()})

    if streaming:
        @router.post("/message:stream")
        async def stream_message(body: dict[str, Any]) -> Response:
            task, text, resuming, context = await _accept(body)
            # **구독을 먼저 건다.** 일을 먼저 시작하면 종결 이벤트가 구독자 0명에게
            # 방송되고, 뒤늦게 붙은 스트림은 그것을 통째로 놓친다 — 이미 끝나 있으면
            # Task 프레임 하나만 내고 닫히고, 아직이면 영원히 기다린다.
            queue = store.subscribe(task.id)
            # **깊은 복사여야 한다.** store.get 은 살아 있는 객체를 돌려주므로, 그냥
            # 들고 있으면 생성기가 도는 시점에는 이미 종결로 바뀌어 있다 — 그러면
            # 첫 프레임이 종결 상태로 나가고 스트림이 바로 닫힌다.
            snapshot = store.get(task.id).model_copy(deep=True)
            runner = asyncio.create_task(
                executor.run(task.id, text, resuming=resuming, context=context))
            store.record(task.id).runner = runner
            return StreamingResponse(_events(task.id, queue, first=snapshot),
                                     media_type="text/event-stream")

        @router.post("/tasks/{task_id}:subscribe")
        async def subscribe(task_id: str) -> Response:
            task = store.get(task_id)
            if task.status.state in TERMINAL_STATES:
                raise UnsupportedOperationError(
                    f"종결된 태스크는 구독할 수 없습니다: {task.status.state.value}")
            return StreamingResponse(
                _events(task_id, store.subscribe(task_id),
                        first=task.model_copy(deep=True)),
                media_type="text/event-stream")

    async def _events(task_id: str, queue: asyncio.Queue, *,
                      first: Task) -> AsyncIterator[bytes]:
        """규격 §3.1.2·§3.5.2 — Task 로 시작하고, 생성 순서를 지키고, **종결에서** 닫는다.

        닫는 조건은 종결 넷(COMPLETED·FAILED·CANCELED·REJECTED)뿐이다.
        `INPUT_REQUIRED` 는 목록에 없다 — 되묻기 왕복이 **같은 스트림에서** 이어지도록
        열어 둔다. 사용자가 답하면 WORKING 으로 돌아오고 이벤트가 계속 흐른다.

        다만 아무도 답하지 않으면 영원히 열려 있으므로 무응답 시한을 둔다. 규격이
        금지하지 않는 구현 선택이고, 안 두면 연결이 샌다.

        `queue` 를 밖에서 받는다. 이 생성기는 클라이언트가 본문을 읽기 시작해야 처음
        도는데, 그때 구독하면 그 사이에 난 이벤트를 놓친다. 구독은 **일이 시작되기
        전에** 걸려 있어야 한다.
        """
        try:
            yield _sse(stream_frame("task", first))
            if first.status.state in TERMINAL_STATES:
                return
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), stream_idle_sec)
                except TimeoutError:
                    return                    # 무응답 — 연결을 붙들고 있지 않는다
                context_id = store.get(task_id).context_id or ""
                if kind == "status":
                    yield _sse(stream_frame("statusUpdate", TaskStatusUpdateEvent(
                        task_id=task_id, context_id=context_id, status=payload.status)))
                    if payload.status.state in TERMINAL_STATES:
                        return
                else:
                    yield _sse(stream_frame("artifactUpdate", TaskArtifactUpdateEvent(
                        task_id=task_id, context_id=context_id, artifact=payload,
                        last_chunk=True)))
        finally:
            store.unsubscribe(task_id, queue)

    # ─────────────────────────────────────── 태스크

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, historyLength: int | None = None) -> Response:
        task = store.get(task_id)
        wire = task.wire()
        if historyLength is not None and "history" in wire:
            wire["history"] = wire["history"][-historyLength:] if historyLength else []
        return _json(wire)

    @router.get("/tasks")
    async def list_tasks(contextId: str | None = None, status: str | None = None,
                         pageSize: int = 50, pageToken: str = "") -> Response:
        if not 1 <= pageSize <= 100:
            raise UnsupportedOperationError("pageSize 는 1~100 이어야 합니다.")
        state = None
        if status:
            try:
                state = TaskState(status)
            except ValueError as exc:
                raise UnsupportedOperationError(
                    f"모르는 상태입니다: {status}") from exc
        offset = int(pageToken) if pageToken.isdigit() else 0
        items, total = store.list(context_id=contextId, state=state,
                                  page_size=pageSize, offset=offset)
        nxt = str(offset + pageSize) if offset + pageSize < total else ""
        return _json(ListTasksResponse(tasks=items, next_page_token=nxt,
                                       page_size=pageSize, total_size=total).wire())

    @router.post("/tasks/{task_id}:cancel")
    async def cancel_task(task_id: str) -> Response:
        return _json(store.cancel(task_id).wire())

    # ─────────────────────────────────────── 푸시 알림

    def _guard_push() -> None:
        if not push_notifications:
            raise PushNotificationNotSupportedError(
                "이 에이전트는 푸시 알림을 지원하지 않습니다.")

    if push_notifications:
        @router.post("/tasks/{task_id}/pushNotificationConfigs")
        async def create_push(task_id: str, body: dict[str, Any]) -> Response:
            store.get(task_id)
            cfg = _validate(TaskPushNotificationConfig, {**body, "taskId": task_id})
            push_configs.setdefault(task_id, {})[cfg.id] = cfg
            return _json(cfg.wire())

        @router.get("/tasks/{task_id}/pushNotificationConfigs")
        async def list_push(task_id: str) -> Response:
            store.get(task_id)
            configs = list(push_configs.get(task_id, {}).values())
            return _json(ListTaskPushNotificationConfigsResponse(configs=configs).wire())

        @router.get("/tasks/{task_id}/pushNotificationConfigs/{config_id}")
        async def get_push(task_id: str, config_id: str) -> Response:
            store.get(task_id)
            cfg = push_configs.get(task_id, {}).get(config_id)
            if cfg is None:
                raise TaskNotFoundError(f"모르는 설정입니다: {config_id}")
            return _json(cfg.wire())

        @router.delete("/tasks/{task_id}/pushNotificationConfigs/{config_id}")
        async def delete_push(task_id: str, config_id: str) -> Response:
            store.get(task_id)
            if push_configs.get(task_id, {}).pop(config_id, None) is None:
                raise TaskNotFoundError(f"모르는 설정입니다: {config_id}")
            return _json({})

    app.include_router(router)
    app.state.card = card
    app.state.store = store
    app.state.push_configs = push_configs
    return app


def _sse(frame: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode()
