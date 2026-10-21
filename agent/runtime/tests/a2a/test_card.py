"""agent.yaml → AgentCard.

카드가 규격의 필수 필드를 갖추는지, 그리고 사람이 쓴 것과 런타임이 아는 것이
올바로 합쳐지는지.
"""

import pytest

from a2a.card import TOOL_HINTS, CardError, build_card, side_effect_of
from a2a.types import HTTP_JSON, PROTOCOL_VERSION


def spec(**over):
    base = {
        "name": "테스트 에이전트",
        "description": "무언가를 한다. 다른 것은 이 에이전트가 아니다.",
        "version": "1.0.0",
        "skills": [{"id": "s1", "name": "스킬", "description": "설명", "tags": ["t"],
                    "hints": {"readOnly": True}}],
    }
    return {**base, **over}


def card(**over):
    return build_card(spec(**over), url="http://a:1")


class TestRequiredFields:
    def test_all_eight_present(self):
        wire = card().wire()
        assert {"name", "description", "supportedInterfaces", "version", "capabilities",
                "defaultInputModes", "defaultOutputModes", "skills"} <= set(wire)

    @pytest.mark.parametrize("missing", ["name", "description", "version", "skills"])
    def test_missing_is_a_startup_error(self, missing):
        """기동 시점에 죽는 편이 낫다 — 카드가 반쯤 빈 채로 뜨는 것보다."""
        broken = spec()
        del broken[missing]
        with pytest.raises(CardError, match=missing):
            build_card(broken, url="http://a:1")

    def test_empty_value_counts_as_missing(self):
        with pytest.raises(CardError):
            build_card(spec(description=""), url="http://a:1")

    def test_defaults_fill_modes(self):
        wire = card().wire()
        assert wire["defaultInputModes"] == ["text/plain"]
        assert "application/json" in wire["defaultOutputModes"]


class TestInterface:
    def test_url_comes_from_runtime_not_yaml(self):
        """주소는 실행 환경이 안다. 사람이 쓰는 파일에 두면 갈라진다."""
        itf = build_card(spec(), url="https://out.example/agents/x/").wire()["supportedInterfaces"][0]
        assert itf["url"] == "https://out.example/agents/x"      # 끝의 / 는 뗀다
        assert itf["protocolBinding"] == HTTP_JSON
        assert itf["protocolVersion"] == PROTOCOL_VERSION


class TestSkills:
    def test_hints_do_not_leak_into_skill(self):
        """AgentSkill 에는 힌트 자리가 없다. 확장으로 가야 한다."""
        assert "hints" not in card().wire()["skills"][0]

    def test_skill_fields_pass_through(self):
        s = build_card(spec(skills=[{"id": "a", "name": "A", "description": "d",
                                     "tags": ["x"], "examples": ["예시"]}]),
                       url="http://a:1").wire()["skills"][0]
        assert s["examples"] == ["예시"]

    def test_duplicate_skill_id_is_rejected(self):
        with pytest.raises(CardError, match="겹친다"):
            build_card(spec(skills=[
                {"id": "same", "name": "A", "description": "d", "tags": ["x"]},
                {"id": "same", "name": "B", "description": "d", "tags": ["x"]},
            ]), url="http://a:1")

    def test_unknown_hint_key_is_rejected(self):
        """오타를 조용히 넘기면 병렬 판단이 틀어진다."""
        with pytest.raises(CardError, match="모르는 키"):
            build_card(spec(skills=[{"id": "a", "name": "A", "description": "d",
                                     "tags": ["x"], "hints": {"readonly": True}}]),
                       url="http://a:1")

    def test_missing_skill_field_is_rejected(self):
        with pytest.raises(CardError, match=r"skills\[0\]"):
            build_card(spec(skills=[{"id": "a", "name": "A"}]),
                       url="http://a:1")


class TestExtensions:
    def _uris(self, c):
        return [e.uri for e in c.capabilities.extensions]

    def test_three_extensions_declared(self):
        assert len(self._uris(card())) == 3

    def test_all_optional(self):
        """모르는 클라이언트가 무시하고도 정상 동작해야 한다."""
        assert all(e.required is False for e in card().capabilities.extensions)

    def test_uris_are_published_constants_not_config(self):
        """확장 URI 는 **설정이 아니다.**

        설정으로 두면 양쪽이 같은 값을 갖도록 밖에서 맞춰야 하고, 그 순간 확장이
        아니라 두 당사자의 사설 관례가 된다 — 제3자가 카드를 읽어도 무엇인지 알 수
        없고, 값이 어긋나면 오류 없이 조용히 무시된다.
        """
        from a2a.extensions import ALL
        assert set(self._uris(card())) == set(ALL)

    def test_hints_land_in_tool_hints_params(self):
        ext = next(e for e in card().capabilities.extensions if TOOL_HINTS in e.uri)
        assert ext.params["skills"]["s1"]["readOnly"] is True

    def test_missing_hint_keys_default_conservatively(self):
        """안 쓴 것은 부작용 있는 쪽으로. 모르면 안전한 쪽이다."""
        ext = next(e for e in card().capabilities.extensions if TOOL_HINTS in e.uri)
        hint = ext.params["skills"]["s1"]
        assert hint == {"readOnly": True, "destructive": False, "idempotent": False}


class TestCapabilitiesMatchRuntime:
    def test_declared_from_runtime_not_yaml(self):
        """§5.3 — 선언한 것은 반드시 구현한다. 사람이 쓰는 파일에서 받지 않는다."""
        c = build_card(spec(), url="http://a:1",
                       streaming=False, push_notifications=False)
        assert c.capabilities.streaming is False
        assert c.capabilities.push_notifications is False

    def test_yaml_cannot_override_capabilities(self):
        c = build_card(spec(capabilities={"streaming": True}),
                       url="http://a:1", streaming=False)
        assert c.capabilities.streaming is False


class TestSideEffectAggregation:
    """§2.5.1 — 보수적 집계. 틀리면 병렬이 조용히 죽는다."""

    def _card(self, *read_flags):
        skills = [{"id": f"s{i}", "name": "S", "description": "d", "tags": ["t"],
                   "hints": {"readOnly": flag}} for i, flag in enumerate(read_flags)]
        return build_card(spec(skills=skills), url="http://a:1")

    def test_all_read_only_is_read(self):
        assert side_effect_of(self._card(True, True)) == "read"

    def test_any_write_makes_the_whole_agent_write(self):
        assert side_effect_of(self._card(True, False)) == "mutate"

    def test_all_write_is_write(self):
        assert side_effect_of(self._card(False, False)) == "mutate"

    def test_no_extension_is_write(self):
        """선언이 없으면 모르는 것이다. 모르면 안전한 쪽."""
        c = card()
        c.capabilities.extensions = []
        assert side_effect_of(c) == "mutate"

    def test_unknown_extension_uri_is_not_recognised(self):
        """다른 URI 는 다른 확장이다 — 이름이 비슷해도 읽지 않는다."""
        c = card()
        c.capabilities.extensions[0].uri = "https://other.example/ext/tool-hints/v1"
        assert side_effect_of(c) == "mutate"


class TestProvider:
    def test_optional(self):
        assert card().provider is None

    def test_both_fields_required_when_present(self):
        with pytest.raises(CardError, match="organization"):
            build_card(spec(provider={"url": "https://x"}),
                       url="http://a:1")

    def test_passes_through(self):
        c = build_card(spec(provider={"organization": "조직", "url": "https://x"}),
                       url="http://a:1")
        assert c.provider.organization == "조직"
