"""규격 정본과의 대조.

이 파일이 이 전환의 안전장치다. `a2a/types.py` 는 규격 문서를 **손으로 읽고 옮긴**
것이라 옮김에 실수가 들어갈 수 있다 — 실제로 설계 중에 필드명을 두 번 틀렸다.

두 방향으로 지킨다.
  ① 규격 문서의 JSON 예시를 픽스처로 박고, 우리 모델이 그대로 파싱하는지
  ② 우리 enum·필수 필드가 proto 정본과 글자 단위로 같은지

`agent-spec/reference/` 의 정본을 읽는다. 못 찾으면 **수집 단계에서 실패한다** —
스킵되면 "대조를 했다"는 착각만 남기 때문이다.
"""

import re
from pathlib import Path

import pytest

from a2a.types import (
    TERMINAL_STATES,
    AgentCard,
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
)


def _find_reference() -> Path:
    """`agent-spec/reference/` 를 위로 올라가며 찾는다.

    경로를 상수로 박으면 디렉터리를 옮길 때 조용히 스킵되고, 그러면 "정본 대조를
    했다"는 착각만 남는다 — 이 파일의 존재 이유가 사라진다. 못 찾으면 스킵이
    아니라 실패다.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "agent-spec" / "reference"
        if (candidate / "a2a.proto").exists():
            return candidate
    raise FileNotFoundError(
        "agent-spec/reference/a2a.proto 를 못 찾았다. 정본 없이는 대조가 성립하지 않는다."
    )


REF = _find_reference()
PROTO = REF / "a2a.proto"
SPEC = REF / "specification.md"


# ─────────────────────────────────────── ① 규격 문서의 JSON 예시를 그대로 파싱

# 규격 docs/specification.md 에서 그대로 옮긴 것. 손대지 않는다.
SPEC_TASK_COMPLETED = {
    "id": "task-uuid",
    "contextId": "context-uuid",
    "status": {"state": "TASK_STATE_COMPLETED"},
}
SPEC_TASK_INPUT_REQUIRED = {
    "id": "task-uuid",
    "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
}
SPEC_MESSAGE = {
    "messageId": "uuid",
    "role": "ROLE_USER",
    "parts": [{"text": "Hello"}],
}
SPEC_STREAM_FRAMES = [
    {"task": {"id": "task-uuid", "contextId": "context-uuid",
              "status": {"state": "TASK_STATE_WORKING"}}},
    {"artifactUpdate": {"taskId": "task-uuid", "contextId": "context-uuid",
                        "artifact": {"parts": [{"text": "# Climate Change Report\n\n"}]}}},
    {"statusUpdate": {"taskId": "task-uuid", "contextId": "context-uuid",
                      "status": {"state": "TASK_STATE_COMPLETED"}}},
]
SPEC_INTERFACES = [
    {"url": "https://georoute-agent.example.com/a2a/v1",
     "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
    {"url": "https://georoute-agent.example.com/a2a/grpc",
     "protocolBinding": "GRPC", "protocolVersion": "1.0"},
    {"url": "https://georoute-agent.example.com/a2a/json",
     "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"},
]


class TestSpecExamplesParse:
    """규격 문서에 실린 예시가 우리 모델을 그대로 통과해야 한다."""

    @pytest.mark.parametrize("payload", [SPEC_TASK_COMPLETED, SPEC_TASK_INPUT_REQUIRED])
    def test_task_examples(self, payload):
        task = Task.model_validate(payload)
        assert task.status.state.value == payload["status"]["state"]

    def test_message_example(self):
        msg = Message.model_validate(SPEC_MESSAGE)
        assert msg.message_id == "uuid"
        assert msg.text() == "Hello"

    def test_stream_frames(self):
        for frame in SPEC_STREAM_FRAMES:
            (kind, payload), = frame.items()
            if kind == "task":
                Task.model_validate(payload)
            elif kind == "artifactUpdate":
                Artifact.model_validate({"artifactId": "a", **payload["artifact"]})
            elif kind == "statusUpdate":
                TaskStatus.model_validate(payload["status"])

    def test_round_trip_preserves_spec_shape(self):
        """파싱했다 내보내도 규격 예시와 같은 키가 나와야 한다."""
        wire = Task.model_validate(SPEC_TASK_COMPLETED).wire()
        for key in SPEC_TASK_COMPLETED:
            assert key in wire, f"{key} 가 사라졌다"

    def test_examples_are_actually_in_the_spec_document(self):
        """픽스처가 규격 문서에서 온 것이 맞는지 — 내가 지어낸 게 아닌지."""
        text = SPEC.read_text(encoding="utf-8")
        for probe in ('"state": "TASK_STATE_COMPLETED"',
                      '"role": "ROLE_USER"',
                      '"protocolBinding": "HTTP+JSON"',
                      '"artifactUpdate"'):
            assert probe in text, f"규격 문서에 없다: {probe}"

    def test_all_three_bindings_parse(self):
        card_ifaces = [i for i in SPEC_INTERFACES]
        for raw in card_ifaces:
            from a2a.types import AgentInterface
            itf = AgentInterface.model_validate(raw)
            assert itf.protocol_version == "1.0"


# ─────────────────────────────────────── ② proto 정본과 글자 단위 대조

@pytest.fixture(scope="module")
def proto() -> str:
    return PROTO.read_text(encoding="utf-8")


class TestAgainstProto:
    def test_task_state_enum_matches_exactly(self, proto):
        """빠져도 남아도 안 된다."""
        block = re.search(r"enum TaskState \{(.*?)\n\}", proto, re.S)
        assert block, "proto 에서 enum TaskState 를 못 찾았다"
        from_proto = set(re.findall(r"(TASK_STATE_[A-Z_]+)\s*=", block.group(1)))
        ours = {s.value for s in TaskState}
        assert ours == from_proto, (
            f"우리에만: {ours - from_proto} / proto 에만: {from_proto - ours}"
        )

    def test_terminal_states_match_proto_comments(self, proto):
        """종결 상태는 proto 주석이 'terminal state' 라고 밝힌 것과 같아야 한다."""
        block = re.search(r"enum TaskState \{(.*?)\n\}", proto, re.S).group(1)
        marked, comment = set(), ""
        for line in block.split("\n"):
            s = line.strip()
            if s.startswith("//"):
                comment += " " + s
                continue
            if m := re.match(r"(TASK_STATE_[A-Z_]+)\s*=", s):
                if "terminal state" in comment.lower():
                    marked.add(m.group(1))
                comment = ""
        assert {s.value for s in TERMINAL_STATES} == marked

    def test_role_enum_matches(self, proto):
        block = re.search(r"enum Role \{(.*?)\n\}", proto, re.S).group(1)
        from a2a.types import Role
        assert {r.value for r in Role} == set(re.findall(r"(ROLE_[A-Z_]+)\s*=", block))

    def _required_of(self, proto: str, message: str) -> set[str]:
        """proto 의 REQUIRED 필드를 camelCase 로 돌려준다."""
        block = re.search(rf"message {message} \{{(.*?)\n\}}", proto, re.S)
        assert block, f"proto 에 message {message} 가 없다"
        out = set()
        for line in block.group(1).split("\n"):
            if "REQUIRED" not in line:
                continue
            m = re.search(r"(\w+)\s*=\s*\d+", line)
            if m:
                head, *rest = m.group(1).split("_")
                out.add(head + "".join(p.title() for p in rest))
        return out

    def test_agent_card_required_fields_match(self, proto):
        required = self._required_of(proto, "AgentCard")
        ours = {f.alias or n for n, f in AgentCard.model_fields.items() if f.is_required()}
        assert ours == required, f"우리에만: {ours - required} / proto 에만: {required - ours}"

    def test_task_required_fields_match(self, proto):
        required = self._required_of(proto, "Task")
        ours = {f.alias or n for n, f in Task.model_fields.items() if f.is_required()}
        assert ours == required

    def test_message_required_fields_match(self, proto):
        required = self._required_of(proto, "Message")
        ours = {f.alias or n for n, f in Message.model_fields.items() if f.is_required()}
        assert ours == required

    def test_artifact_required_fields_match(self, proto):
        required = self._required_of(proto, "Artifact")
        ours = {f.alias or n for n, f in Artifact.model_fields.items() if f.is_required()}
        assert ours == required

    def test_agent_skill_has_no_input_schema_in_proto(self, proto):
        """설계 판단의 근거 — 스킬에 스키마가 없다는 사실이 proto 에 있는지."""
        block = re.search(r"message AgentSkill \{(.*?)\n\}", proto, re.S).group(1)
        assert "input_schema" not in block
        assert "annotations" not in block

    def test_part_oneof_members_match(self, proto):
        block = re.search(r"message Part \{(.*?)\n\}", proto, re.S).group(1)
        oneof = re.search(r"oneof content \{(.*?)\n  \}", block, re.S).group(1)
        names = set(re.findall(r"(\w+)\s*=\s*\d+", oneof))
        assert names == {"text", "raw", "url", "data"}
