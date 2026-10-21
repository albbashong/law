"""a2a/types.py — 와이어 규칙(규격 §5.5·§5.6)을 지키는지.

여기서 깨지면 상대가 우리 JSON 을 못 읽는다. 눈으로는 안 잡히는 종류라 테스트로만
지킬 수 있다.
"""

import re

import pytest
from pydantic import ValidationError

from a2a.types import (
    INTERRUPTED_STATES,
    TERMINAL_STATES,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    now_iso,
    stream_frame,
)

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class TestWireNaming:
    def test_fields_are_camel_case(self):
        wire = Task(id="t1", context_id="c1",
                    status=TaskStatus(state=TaskState.WORKING)).wire()
        assert "contextId" in wire
        assert "context_id" not in wire

    def test_nested_fields_are_camel_case(self):
        wire = Message(message_id="m1", role=Role.USER, parts=[Part.of_text("hi")],
                       task_id="t1", context_id="c1").wire()
        assert {"messageId", "taskId", "contextId"} <= set(wire)
        assert not any("_" in k for k in wire)

    def test_artifact_id_is_camel_case(self):
        assert "artifactId" in Artifact.text("t1", "answer", "x").wire()

    def test_media_type_is_camel_case(self):
        part = Part.of_data([1, 2]).wire()
        assert part["mediaType"] == "application/json"

    def test_python_side_stays_snake_case(self):
        """도메인 코드가 와이어 규칙을 몰라도 되어야 한다."""
        m = Message(message_id="m1", role=Role.USER, parts=[], task_id="t1")
        assert m.task_id == "t1"


class TestEnumSerialization:
    def test_state_is_protojson_string_name(self):
        wire = Task(id="t", status=TaskStatus(state=TaskState.INPUT_REQUIRED)).wire()
        assert wire["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    def test_role_is_protojson_string_name(self):
        wire = Message(message_id="m1", role=Role.AGENT, parts=[Part.of_text("x")]).wire()
        assert wire["role"] == "ROLE_AGENT"

    @pytest.mark.parametrize("state", list(TaskState))
    def test_every_state_round_trips(self, state):
        wire = Task(id="t", status=TaskStatus(state=state)).wire()
        assert wire["status"]["state"] == state.value
        assert state.value.startswith("TASK_STATE_")

    def test_terminal_and_interrupted_do_not_overlap(self):
        assert not (TERMINAL_STATES & INTERRUPTED_STATES)

    def test_lowercase_state_is_rejected(self):
        with pytest.raises(ValidationError):
            TaskStatus(state="working")


class TestTimestamp:
    def test_format_is_milliseconds_utc(self):
        assert TS_RE.match(now_iso()), now_iso()

    def test_task_status_gets_timestamp_by_default(self):
        wire = Task(id="t", status=TaskStatus(state=TaskState.WORKING)).wire()
        assert TS_RE.match(wire["status"]["timestamp"])

    def test_never_carries_offset_other_than_z(self):
        """규격 §5.6.1 — Z 외의 오프셋은 MUST NOT."""
        assert now_iso().endswith("Z")
        assert "+" not in now_iso()


class TestFieldPresence:
    def test_unset_optional_fields_are_omitted(self):
        """규격 §5.7 — 설정 안 한 optional 은 JSON 에서 생략한다. null 로 채우지 않는다."""
        wire = Task(id="t", status=TaskStatus(state=TaskState.WORKING)).wire()
        assert "artifacts" not in wire
        assert "metadata" not in wire
        assert None not in wire.values()

    def test_required_field_missing_is_rejected(self):
        with pytest.raises(ValidationError):
            Task(status=TaskStatus(state=TaskState.WORKING))     # id 없음

    def test_unknown_field_is_rejected_on_our_types(self):
        with pytest.raises(ValidationError):
            Task(id="t", status=TaskStatus(state=TaskState.WORKING), bogus=1)


class TestArtifactConvention:
    def test_artifact_id_is_task_scoped(self):
        """§2.6 — {taskId}:{name}. 폴링 클라이언트가 이름으로 대조한다."""
        assert Artifact.text("t7", "answer", "x").artifact_id == "t7:answer"

    def test_data_artifact_declares_json_media_type(self):
        a = Artifact.data("t7", "actions", [{"type": "x"}])
        assert a.parts[0].media_type == "application/json"


class TestMessageHelpers:
    def test_agent_text_fills_task_and_context(self):
        m = Message.agent_text("어느 것입니까?", task_id="t1", context_id="c1")
        assert (m.role, m.task_id, m.context_id) == (Role.AGENT, "t1", "c1")

    def test_text_joins_text_parts_only(self):
        m = Message(message_id="m1", role=Role.USER,
                    parts=[Part.of_text("a"), Part.of_data({"k": 1}), Part.of_text("b")])
        assert m.text() == "a\nb"

    def test_message_id_is_required(self):
        """규격상 REQUIRED. 기본값을 주면 클라이언트의 누락을 우리가 덮어 준다."""
        with pytest.raises(ValidationError):
            Message(role=Role.USER, parts=[Part.of_text("x")])

    def test_text_is_empty_when_no_text_parts(self):
        assert Message(message_id="m1", role=Role.USER, parts=[Part.of_data({})]).text() == ""


class TestStreamFrame:
    def test_oneof_key_wraps_payload(self):
        """규격 §11.7 — oneof 이름이 그대로 JSON 키다. 안 감싸면 구별이 안 된다."""
        frame = stream_frame("statusUpdate",
                             TaskStatus(state=TaskState.COMPLETED))
        assert list(frame) == ["statusUpdate"]
        assert frame["statusUpdate"]["state"] == "TASK_STATE_COMPLETED"


class TestAgentCard:
    def _card(self, **kw):
        base = dict(
            name="n", description="d", version="1.0.0",
            supported_interfaces=[AgentInterface(url="http://x")],
            capabilities=AgentCapabilities(streaming=False),
            default_input_modes=["text/plain"], default_output_modes=["text/plain"],
            skills=[AgentSkill(id="s", name="S", description="d", tags=["t"])],
        )
        return AgentCard(**{**base, **kw})

    def test_required_fields_present_in_wire(self):
        wire = self._card().wire()
        assert {"name", "description", "supportedInterfaces", "version", "capabilities",
                "defaultInputModes", "defaultOutputModes", "skills"} <= set(wire)

    def test_card_has_no_id_field(self):
        """규격에 AgentCard.id 는 없다. 정체는 name + 인터페이스 URL 이다."""
        assert "id" not in self._card().wire()

    def test_interface_defaults_to_http_json_v1(self):
        itf = self._card().wire()["supportedInterfaces"][0]
        assert itf["protocolBinding"] == "HTTP+JSON"
        assert itf["protocolVersion"] == "1.0"

    def test_skill_has_no_input_schema(self):
        """스킬은 호출 시그니처가 아니라 라우팅용 설명문이다(§2.3.3)."""
        assert "inputSchema" not in self._card().wire()["skills"][0]

    def test_missing_required_card_field_is_rejected(self):
        with pytest.raises(ValidationError):
            AgentCard(name="n", description="d", version="1")
