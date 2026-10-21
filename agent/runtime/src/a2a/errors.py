"""A2A 오류 → RFC 9457 problem+json.

규격 §5.4 의 매핑표를 그대로 옮겼다. `tests/a2a/test_errors.py` 가 정본의 표와
대조해 지킨다 — 코드를 400 이라고 잘못 적어도 눈으로는 안 잡힌다.
"""

from __future__ import annotations

from typing import Any

ERROR_BASE = "https://a2a.dev/errors/"


class A2AError(Exception):
    """모든 A2A 오류의 밑바닥. 하위가 `status` 를 정한다."""

    status: int = 400
    name: str = "A2AError"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or self.name)
        self.detail = detail or self.name
        self.extra = extra

    def problem(self) -> dict[str, Any]:
        """RFC 9457 본문. `Content-Type: application/problem+json` 으로 낸다."""
        body: dict[str, Any] = {
            "type": f"{ERROR_BASE}{self.name}",
            "title": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        body.update(self.extra)
        return body


class TaskNotFoundError(A2AError):
    status, name = 404, "TaskNotFoundError"


class InvalidAgentResponseError(A2AError):
    status, name = 500, "InvalidAgentResponseError"


class TaskNotCancelableError(A2AError):
    status, name = 400, "TaskNotCancelableError"


class PushNotificationNotSupportedError(A2AError):
    status, name = 400, "PushNotificationNotSupportedError"


class UnsupportedOperationError(A2AError):
    status, name = 400, "UnsupportedOperationError"


class ContentTypeNotSupportedError(A2AError):
    status, name = 400, "ContentTypeNotSupportedError"


class ExtendedAgentCardNotConfiguredError(A2AError):
    status, name = 400, "ExtendedAgentCardNotConfiguredError"


class ExtensionSupportRequiredError(A2AError):
    status, name = 400, "ExtensionSupportRequiredError"


class VersionNotSupportedError(A2AError):
    status, name = 400, "VersionNotSupportedError"


# ── 표준(JSON-RPC 유래) 오류 — 규격 §5.4 "Standard JSON-RPC Error Codes".
# HTTP 바인딩에서는 둘 다 400 Bad Request(규격 §5.1 "Validation Errors"). A2A 고유
# 아홉 가지(ALL_ERRORS)와는 별개다 — 정본 표 대조 테스트는 그 아홉만 센다.


class JSONParseError(A2AError):
    """본문이 JSON 이 아니다(-32700)."""

    status, name = 400, "JSONParseError"


class InvalidParamsError(A2AError):
    """JSON 이긴 한데 요청 모양이 아니다 — 필수 필드 누락, 타입 불일치(-32602)."""

    status, name = 400, "InvalidParamsError"


#: 규격 §5.4 의 아홉 가지. 테스트가 이 표를 정본과 대조한다.
ALL_ERRORS: tuple[type[A2AError], ...] = (
    TaskNotFoundError, InvalidAgentResponseError, TaskNotCancelableError,
    PushNotificationNotSupportedError, UnsupportedOperationError,
    ContentTypeNotSupportedError, ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError, VersionNotSupportedError,
)
