"""도구 결과의 모델 뷰 — facts 계약(스펙 §5.2).

permit MCP 는 모델이 볼 사실을 `facts` 로 이미 골라 보낸다(부피 제거, 7,600자 안).
원문(31,275자)을 통째로 자르면 조사 6단계 중 1단계만 모델에 닿는다.
"""

import json

import loop
from config import settings

SURVEY = {
    "status": "ok",
    "pnu": "4729011700107010017",
    "facts": {"status": "ok", "pnu": "4729011700107010017",
              "steps": {"parcelAll": {"면적": "13,593"}}},
    "message": "6단계가 끝났다.",
    "input": {"query": "경산시 남매로 159"},
    "resolve": {"firstGeom": "MULTIPOLYGON(((" + "1 1, " * 4000 + "1 1)))"},
    "steps": [{"name": "parcelAll", "result": {"raw": "x" * 20000}}],
    "_pwmcp_meta": {"elapsed_ms": 1.0},
}


def test_facts_contract_hides_bulk_and_keeps_facts():
    text = loop._for_model(SURVEY)
    shown = json.loads(text)
    assert set(shown) == {"status", "pnu", "facts", "message"}
    assert shown["facts"]["steps"]["parcelAll"]["면적"] == "13,593"
    assert "MULTIPOLYGON" not in text and "xxxx" not in text
    assert "생략" not in text and "잘린" not in text


def test_model_view_passes_through_without_facts():
    plain = {"success": True, "center": [1.0, 2.0]}
    assert loop.model_view(plain) == plain
    assert loop.model_view("문자열") == "문자열"
    assert loop.model_view({"facts": {}}) == {"facts": {}}     # 빈 facts 는 계약이 아니다


def test_without_facts_still_truncates(monkeypatch):
    monkeypatch.setattr(settings, "tool_result_max_chars", 100)
    text = loop._for_model({"blob": "y" * 500})
    assert len(text) > 100 and "100자까지만" in text and "없다'고 단정하지" in text


def test_facts_view_respects_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "tool_result_max_chars", 60)
    text = loop._for_model({"facts": {"k": "v" * 200}, "message": "m"})
    assert "60자까지만" in text


def test_default_ceiling_fits_permit_facts_budget():
    """permit MCP facts 예산 7,600 + message 300 + 키 여유가 들어가야 한다."""
    assert settings.tool_result_max_chars >= 9000
