"""A2A Protocol v1.0 데이터 모델.

정본은 `agent-spec/reference/a2a.proto`(커밋 98279d6, 2026-10-20). 이 파일은 그
proto 를 pydantic 으로 옮긴 것이고, `tests/a2a/test_conformance.py` 가 규격 문서의
JSON 예시를 픽스처로 박아 옮김이 어긋나지 않았는지 지킨다.

와이어 규칙 세 가지가 이 파일의 존재 이유다(규격 §5.5·§5.6).

  - 필드명은 camelCase. proto 의 snake_case 가 아니다.
  - enum 은 ProtoJSON 문자열 이름 그대로: "TASK_STATE_WORKING" 이지 "working" 이 아니다.
  - 타임스탬프는 밀리초까지 쓴 UTC 이고 `Z` 로 끝난다. 다른 오프셋은 MUST NOT.

파이썬 쪽에서는 snake_case 로 읽고 쓰되(`populate_by_name`), 밖으로 나갈 때만
camelCase 가 된다 — 그래서 도메인 코드가 와이어 규칙을 몰라도 된다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PROTOCOL_VERSION = "1.0"
HTTP_JSON = "HTTP+JSON"


def now_iso() -> str:
    """규격 §5.6.1 이 정한 타임스탬프. 밀리초 3자리, 언제나 `Z`."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


class A2AModel(BaseModel):
    """모든 A2A 타입의 밑바닥.

    `exclude_none` 이 기본인 이유: 규격 §5.7 이 "설정하지 않은 optional 필드는
    JSON 에서 생략해야 한다"고 정한다. `null` 로 채우면 카드 서명의 정규화가
    깨지고, 받는 쪽이 "명시적으로 비움"과 "설정 안 함"을 구별하지 못한다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )

    def wire(self) -> dict[str, Any]:
        """밖으로 내보낼 JSON. camelCase 이고 빈 필드는 빠진다."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


# ─────────────────────────────────────────────────────────── enum

class TaskState(StrEnum):
    """규격 `a2a.proto:187`. 값이 곧 와이어 표현이다.

    종결(terminal)은 되돌아올 수 없고, 중단(interrupted)은 메시지를 더 받으면
    WORKING 으로 돌아온다. 이 구분이 `store.transition` 의 전이표를 결정한다.
    """

    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


TERMINAL_STATES = frozenset({
    TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED,
})
INTERRUPTED_STATES = frozenset({TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED})


class Role(StrEnum):
    UNSPECIFIED = "ROLE_UNSPECIFIED"
    USER = "ROLE_USER"        # 클라이언트 → 서버
    AGENT = "ROLE_AGENT"      # 서버 → 클라이언트


# ─────────────────────────────────────────────────────────── 내용

class Part(A2AModel):
    """내용 조각. `text`/`raw`/`url`/`data` 는 proto 의 oneof 라 하나만 채운다.

    검증하지 않는 이유: proto oneof 는 나중 대입이 앞을 지우는 의미라 위반이
    성립하지 않고, 받는 쪽에서 여러 개가 온 경우 관용적으로 읽는 편이 낫다.
    """

    text: str | None = None
    raw: str | None = None          # base64
    url: str | None = None
    data: Any = None
    metadata: dict[str, Any] | None = None
    filename: str | None = None
    media_type: str | None = None

    @classmethod
    def of_text(cls, text: str) -> Part:
        return cls(text=text)

    @classmethod
    def of_data(cls, data: Any, media_type: str = "application/json") -> Part:
        return cls(data=data, media_type=media_type)


class Message(A2AModel):
    """한 번의 발화.

    `task_id` 가 있으면 **그 태스크를 이어서** 처리한다 — 되묻기 재개가 이것으로
    돌아간다(규격 §3.4.3). 없으면 새 태스크를 연다.

    `message_id` 는 규격상 REQUIRED 다. 기본값을 주면 편하지만, 그러면 클라이언트가
    빠뜨린 메시지도 조용히 받아들여 규격 위반을 우리가 덮어 준다. 우리가 만드는
    쪽은 `Message.agent_text` 나 `new_id("m_")` 로 채운다.
    """

    message_id: str
    context_id: str | None = None
    task_id: str | None = None
    role: Role
    parts: list[Part]
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None
    reference_task_ids: list[str] | None = None

    @classmethod
    def agent_text(cls, text: str, *, task_id: str, context_id: str) -> Message:
        return cls(message_id=new_id("m_"), role=Role.AGENT, parts=[Part.of_text(text)],
                   task_id=task_id, context_id=context_id)

    def text(self) -> str:
        """모든 텍스트 파트를 이어 붙인다. 텍스트가 없으면 빈 문자열."""
        return "\n".join(p.text for p in self.parts if p.text)


class Artifact(A2AModel):
    artifact_id: str
    name: str | None = None
    description: str | None = None
    parts: list[Part]
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None

    @classmethod
    def text(cls, task_id: str, name: str, body: str) -> Artifact:
        return cls(artifact_id=f"{task_id}:{name}", name=name, parts=[Part.of_text(body)])

    @classmethod
    def data(cls, task_id: str, name: str, body: Any) -> Artifact:
        return cls(artifact_id=f"{task_id}:{name}", name=name, parts=[Part.of_data(body)])


class TaskStatus(A2AModel):
    state: TaskState
    message: Message | None = None
    timestamp: str | None = Field(default_factory=now_iso)


class Task(A2AModel):
    id: str
    context_id: str | None = None
    status: TaskStatus
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    metadata: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────── 카드

class AgentProvider(A2AModel):
    url: str
    organization: str


class AgentExtension(A2AModel):
    uri: str
    description: str | None = None
    required: bool = False
    params: dict[str, Any] | None = None


class AgentCapabilities(A2AModel):
    streaming: bool | None = None
    push_notifications: bool | None = None
    extensions: list[AgentExtension] | None = None
    extended_agent_card: bool | None = None


class AgentSkill(A2AModel):
    """라우팅용 설명문. **입력 스키마가 없다**(규격 `a2a.proto:436`).

    스킬을 나눠도 엔드포인트가 늘지 않는다 — 호출구는 언제나 `message:send` 다.
    오케스트레이터는 `description`/`tags`/`examples` 만 보고 어디로 보낼지 고른다.
    """

    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security_requirements: list[dict[str, Any]] | None = None


class AgentInterface(A2AModel):
    url: str
    protocol_binding: str = HTTP_JSON
    tenant: str | None = None
    protocol_version: str = PROTOCOL_VERSION


class AgentCard(A2AModel):
    """규격 `a2a.proto:362`. **`id` 필드는 없다** — 정체는 name + 인터페이스 URL 이다."""

    name: str
    description: str
    supported_interfaces: list[AgentInterface]
    version: str
    capabilities: AgentCapabilities
    default_input_modes: list[str]
    default_output_modes: list[str]
    skills: list[AgentSkill]
    provider: AgentProvider | None = None
    documentation_url: str | None = None
    security_schemes: dict[str, Any] | None = None
    security_requirements: list[dict[str, Any]] | None = None
    signatures: list[dict[str, Any]] | None = None
    icon_url: str | None = None


# ─────────────────────────────────────────────────────────── 요청·응답

class SendMessageConfiguration(A2AModel):
    accepted_output_modes: list[str] | None = None
    task_push_notification_config: dict[str, Any] | None = None
    history_length: int | None = None
    return_immediately: bool = False


class SendMessageRequest(A2AModel):
    """`extra="forbid"` 를 풀어 둔다 — 클라이언트가 모르는 필드를 붙여도 거절하지
    않는 편이 상호운용에 낫다. 우리가 읽는 것만 읽는다."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    tenant: str | None = None
    message: Message
    configuration: SendMessageConfiguration | None = None
    metadata: dict[str, Any] | None = None


class ListTasksResponse(A2AModel):
    tasks: list[Task]
    next_page_token: str = ""
    page_size: int = 0
    total_size: int = 0


class TaskPushNotificationConfig(A2AModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    id: str = Field(default_factory=lambda: new_id("pnc_"))
    task_id: str | None = None
    url: str
    token: str | None = None
    authentication: dict[str, Any] | None = None
    tenant: str | None = None


class ListTaskPushNotificationConfigsResponse(A2AModel):
    configs: list[TaskPushNotificationConfig]
    next_page_token: str = ""


# ─────────────────────────────────────────────────────────── 스트리밍

class TaskStatusUpdateEvent(A2AModel):
    task_id: str
    context_id: str
    status: TaskStatus
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(A2AModel):
    task_id: str
    context_id: str
    artifact: Artifact
    append: bool = False
    last_chunk: bool = False
    metadata: dict[str, Any] | None = None


def stream_frame(kind: Literal["task", "message", "statusUpdate", "artifactUpdate"],
                 payload: A2AModel) -> dict[str, Any]:
    """`StreamResponse` 의 oneof 를 JSON 으로 편다.

    proto 의 oneof 이름이 그대로 JSON 키가 된다(규격 §11.7 예시):
    `{"statusUpdate": {...}}`. 감싸지 않고 내보내면 받는 쪽이 어느 이벤트인지
    구별하지 못한다.
    """
    return {kind: payload.wire()}
