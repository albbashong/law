"""오류 → HTTP 매핑이 규격 §5.4 표와 일치하는지.

코드를 400 이라고 잘못 적어도 눈으로는 안 잡힌다. 정본의 표와 직접 대조한다.
"""

import re
from pathlib import Path

import pytest

from a2a.errors import (
    ALL_ERRORS,
    A2AError,
    TaskNotFoundError,
    UnsupportedOperationError,
)


def _find_spec() -> Path:
    for parent in Path(__file__).resolve().parents:
        p = parent / "agent-spec" / "reference" / "specification.md"
        if p.exists():
            return p
    raise FileNotFoundError("규격 문서를 못 찾았다 — 대조가 성립하지 않는다.")


class TestAgainstSpecTable:
    @pytest.fixture(scope="class")
    @staticmethod
    def spec_mapping() -> dict[str, int]:
        """규격 §5.4 의 표에서 오류→HTTP 를 뽑는다."""
        text = _find_spec().read_text(encoding="utf-8")
        rows = re.findall(r"\|\s*`(\w+Error)`\s*\|[^|]*\|[^|]*\|\s*`(\d{3})", text)
        assert rows, "규격 문서에서 오류 매핑 표를 못 찾았다"
        return {name: int(code) for name, code in rows}

    def test_every_error_matches_spec_status(self, spec_mapping):
        for cls in ALL_ERRORS:
            assert cls.name in spec_mapping, f"{cls.name} 이 규격 표에 없다"
            assert cls.status == spec_mapping[cls.name], (
                f"{cls.name}: 우리 {cls.status} vs 규격 {spec_mapping[cls.name]}")

    def test_we_cover_every_error_in_spec(self, spec_mapping):
        ours = {c.name for c in ALL_ERRORS}
        assert ours == set(spec_mapping), f"규격에만: {set(spec_mapping) - ours}"

    def test_not_found_is_404(self, spec_mapping):
        assert spec_mapping["TaskNotFoundError"] == 404

    def test_invalid_agent_response_is_500(self, spec_mapping):
        assert spec_mapping["InvalidAgentResponseError"] == 500


class TestProblemJson:
    def test_shape_is_rfc9457(self):
        body = TaskNotFoundError("모르는 태스크입니다: t9").problem()
        assert body == {
            "type": "https://a2a.dev/errors/TaskNotFoundError",
            "title": "TaskNotFoundError",
            "status": 404,
            "detail": "모르는 태스크입니다: t9",
        }

    def test_detail_defaults_to_name(self):
        assert UnsupportedOperationError().problem()["detail"] == "UnsupportedOperationError"

    def test_extra_fields_are_merged(self):
        body = TaskNotFoundError("x", taskId="t9").problem()
        assert body["taskId"] == "t9"

    def test_is_an_exception(self):
        with pytest.raises(A2AError):
            raise TaskNotFoundError("x")


class TestStandardErrors:
    """규격 §5.4 표준(JSON-RPC 유래) 오류 — HTTP 바인딩에서는 400(규격 §5.1 Validation Errors)."""

    def test_invalid_params_is_400(self):
        from a2a.errors import InvalidParamsError
        assert InvalidParamsError.status == 400
        assert InvalidParamsError("messageId: 필수").problem()["title"] == "InvalidParamsError"

    def test_json_parse_is_400(self):
        from a2a.errors import JSONParseError
        assert JSONParseError.status == 400

    def test_standard_errors_are_not_in_a2a_specific_table(self):
        """ALL_ERRORS 는 A2A 고유 아홉 가지만 — 정본 표 대조 테스트가 그 전제로 돈다."""
        from a2a.errors import InvalidParamsError, JSONParseError
        assert InvalidParamsError not in ALL_ERRORS and JSONParseError not in ALL_ERRORS
