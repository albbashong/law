"""도로접면 분류표는 코드가 아니라 데이터 파일(ROAD_SIDE_PATH)에서 읽는다."""
import json

from law_mcp.config import Settings

KEYS = ("road_side_terms", "road_side_decisive", "road_side_note",
        "road_side_decisive_text", "road_side_check_text", "source_road_text")


def test_code_has_no_road_side_vocabulary():
    assert all(not Settings.model_fields[k].default for k in KEYS)


def test_road_side_vocabulary_comes_from_the_file(tmp_path):
    f = tmp_path / "road.json"
    f.write_text(json.dumps({"road_side_terms": {"가": "가: 뜻"}, "road_side_decisive": ["가"],
                             "road_side_note": "n", "road_side_decisive_text": "d",
                             "road_side_check_text": "c", "source_road_text": "s"}), encoding="utf-8")
    s = Settings(road_side_path=f)
    assert s.road_side_terms == {"가": "가: 뜻"} and s.road_side_decisive == ["가"]
    assert (s.road_side_note, s.road_side_decisive_text, s.road_side_check_text, s.source_road_text) == ("n", "d", "c", "s")


def test_env_value_wins_over_the_file(tmp_path):
    f = tmp_path / "road.json"
    f.write_text(json.dumps({"road_side_note": "file"}), encoding="utf-8")
    assert Settings(road_side_path=f, road_side_note="env").road_side_note == "env"


def test_shipped_file_fills_the_vocabulary():
    s = Settings()
    assert s.road_side_terms and s.road_side_decisive and s.source_road_text
