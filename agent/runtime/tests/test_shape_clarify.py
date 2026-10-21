"""되묻기 성형 — permit 도구의 needs_disambiguation → 규격 candidates (스펙 §5.3)."""

import pytest

import shape
from config import settings

CANDS = [
    {"pnu": "4729010700108310001",
     "road_address": "경상북도 경산시 경산로 21 (옥곡동, 부영조은마을)",
     "jibun_address": "경상북도 경산시 옥곡동 831-1 부영조은마을",
     "building_name": "부영조은마을"},
    {"pnu": "4729010700108310002",
     "road_address": "경상북도 경산시 경산로 23",
     "jibun_address": "경상북도 경산시 옥곡동 831-2"},
]


def ambiguous(query="경산시 부영"):
    return {"status": "needs_disambiguation", "pnu": None,
            "facts": {"status": "needs_disambiguation", "query": query,
                      "total_count": 2, "candidates": CANDS},
            "message": "후보가 여럿이다. 하나를 골라 pnu 로 다시 부르라.",
            "resolve": {"status": "ambiguous", "candidates": CANDS, "firstGeom": None},
            "steps": [{"name": "resolve", "result": {"raw": "x" * 1000}}],
            "_pwmcp_meta": {"elapsed_ms": 3.0}}


@pytest.fixture(autouse=True)
def permit_settings(monkeypatch):
    monkeypatch.setattr(settings, "clarify_tools", "parcel_land_survey,parcel_spatial_survey")
    monkeypatch.setattr(settings, "clarify_status_values", "clarify,needs_disambiguation")
    monkeypatch.setattr(settings, "candidate_id_keys", "pnu,id,code,sku")
    monkeypatch.setattr(settings, "resolve_drop_keys", "steps,_pwmcp_meta")


class TestFacts:
    """도구가 밝힌 사실을 모아 올린다 — 코어가 그것을 사실 레지스터에 앉힌다(결정 6)."""

    def test_merges_the_facts_every_tool_declared(self):
        """뒤 도구가 같은 키를 덮는다. 어떤 키가 무슨 뜻인지 런타임은 모른다."""
        out = shape.shape_result("답", {
            "resolve_location": {"status": "ok",
                                 "facts": {"pnu": "P1", "address": "관양동 1023-4"}},
            "parcel_land_survey": {"status": "ok",
                                   "facts": {"pnu": "P1", "land_category": "대"}},
        })
        assert out["facts"] == {"pnu": "P1", "address": "관양동 1023-4", "land_category": "대"}

    def test_is_empty_when_no_tool_declared_any(self):
        assert shape.shape_result("답", {"t": {"status": "ok"}})["facts"] == {}


class TestProjectCandidates:
    def test_keeps_id_and_builds_label(self):
        out = shape.project_candidates(CANDS, ("pnu", "id"))
        assert out[0]["pnu"] == "4729010700108310001"
        assert out[0]["label"] == "경상북도 경산시 경산로 21 (옥곡동, 부영조은마을)"
        assert out[1]["label"] == "경상북도 경산시 경산로 23"
        assert out[0]["jibun_address"].startswith("경상북도")            # 원본 필드는 남는다

    def test_building_name_is_prefixed_when_not_in_label(self):
        out = shape.project_candidates(
            [{"pnu": "1", "jibun_address": "옥곡동 831-1", "building_name": "부영"}], ("pnu",))
        assert out[0]["label"] == "부영 (옥곡동 831-1)"

    def test_duplicate_labels_are_disambiguated_by_id(self):
        out = shape.project_candidates(
            [{"pnu": "1", "road_address": "같은 주소"}, {"pnu": "2", "road_address": "같은 주소"}],
            ("pnu",))
        assert out[0]["label"] != out[1]["label"]
        assert out[0]["label"].endswith("[1]") and out[1]["label"].endswith("[2]")

    def test_item_without_id_gets_index(self):
        out = shape.project_candidates(
            [{"road_address": "a"}, {"road_address": "b"}], ("pnu", "id"))
        assert [c["id"] for c in out] == ["0", "1"]

    def test_non_list_is_empty(self):
        assert shape.project_candidates(None, ("pnu",)) == []
        assert shape.project_candidates({"pnu": "1"}, ("pnu",)) == []

    def test_tool_supplied_label_wins(self):
        """M2 — 도구가 label 을 스스로 밝혔으면 그것을 쓴다(여전히 중복은 가른다)."""
        out = shape.project_candidates([{"id": "a", "label": "첫째"}], ("pnu", "id"))
        assert out[0]["label"] == "첫째"
        assert out[0]["id"] == "a"

    def test_label_of_falls_back_to_joining_string_fields_when_no_address(self):
        out = shape.project_candidates([{"pnu": "1", "name": "x", "note": "y"}], ("pnu",))
        assert "x" in out[0]["label"] and "y" in out[0]["label"]

    def test_non_mapping_item_in_the_list_is_skipped(self):
        out = shape.project_candidates([{"pnu": "1"}, "문자열", None, {"pnu": "2"}], ("pnu",))
        assert [c["pnu"] for c in out] == ["1", "2"]


class TestShapeResult:
    def test_needs_disambiguation_becomes_clarify(self):
        shaped = shape.shape_result("", {"parcel_land_survey": ambiguous()})
        assert shaped["status"] == "clarify"
        assert [c["pnu"] for c in shaped["candidates"]] == [c["pnu"] for c in CANDS]
        assert shaped["message"] == "후보가 여럿이다. 하나를 골라 pnu 로 다시 부르라."

    def test_model_answer_is_the_question_when_present(self):
        shaped = shape.shape_result("두 곳입니다. 1) 경산로 21 2) 경산로 23 — 어느 곳입니까?",
                                    {"parcel_land_survey": ambiguous()})
        assert shaped["status"] == "clarify"
        assert shaped["message"].startswith("두 곳입니다")

    def test_resolve_drops_bulk_keys(self):
        shaped = shape.shape_result("", {"parcel_land_survey": ambiguous()})
        assert "steps" not in shaped["resolve"] and "_pwmcp_meta" not in shaped["resolve"]
        assert shaped["resolve"]["resolve"]["candidates"] == CANDS

    def test_latest_called_clarify_tool_wins(self):
        results = {"parcel_land_survey": {"status": "ok", "pnu": "1", "facts": {"a": 1}},
                   "parcel_spatial_survey": ambiguous()}
        assert shape.shape_result("", results)["status"] == "clarify"
        results = {"parcel_spatial_survey": ambiguous(),
                   "parcel_land_survey": {"status": "ok", "pnu": "1", "facts": {"a": 1}}}
        shaped = shape.shape_result("답", results)
        assert shaped["status"] == "ok" and shaped["resolve"]["pnu"] == "1"

    def test_ok_result_is_not_clarify_and_keeps_resolve(self):
        ok = {"status": "ok", "pnu": "1", "facts": {"lon": 1.0}, "resolve": {"firstGeom": "WKT"},
              "steps": [], "_pwmcp_meta": {}}
        shaped = shape.shape_result("답", {"parcel_land_survey": ok})
        assert shaped["status"] == "ok" and shaped["candidates"] == []
        assert shaped["resolve"] == {"status": "ok", "pnu": "1", "facts": {"lon": 1.0},
                                     "resolve": {"firstGeom": "WKT"}}

    def test_tool_outside_clarify_tools_is_ignored(self):
        shaped = shape.shape_result("답", {"other_tool": ambiguous()})
        assert shaped["status"] == "ok" and shaped["resolve"] is None

    def test_no_clarify_tools_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "clarify_tools", "")
        shaped = shape.shape_result("답", {"parcel_land_survey": ambiguous()})
        assert shaped["status"] == "ok"

    def test_actions_from_tool_results_are_collected(self):
        shaped = shape.shape_result("답", {"t": {"actions": [{"type": "map.flyTo"}]}})
        assert shaped["actions"] == [{"type": "map.flyTo"}]

    def test_usage_defaults_to_zero(self):
        assert shape.shape_result("답", {})["usage"] == {"tokens_in": 0, "tokens_out": 0}
