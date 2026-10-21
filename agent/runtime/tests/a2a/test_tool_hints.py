"""실제 `agent.yaml` 로 병렬 집계를 확인한다 — §2.5.1 회귀 방지.

이 프로젝트의 에이전트들이 대상이다. 누가 `hints` 를 지우거나 값을 바꾸면 여기서
깨진다. 그러지 않으면 **오류 없이 병렬이 죽는다** — 결과는 맞게 나오고 느려질 뿐이라
기능 테스트로도 안 잡힌다.
"""

from pathlib import Path

import pytest

from a2a.card import load_card, side_effect_of

AGENTS = Path(__file__).resolve().parents[3]

#: 스펙 §6 의 선언. 바뀌면 병렬 거동이 바뀐다.
EXPECTED = {
    "law_review": "read",       # 법령 근거 조회만 한다 — 다른 읽기와 병렬
}


def _cards():
    for name, expected in EXPECTED.items():
        path = AGENTS / name / "agent.yaml"
        if path.exists():
            yield name, path, expected


@pytest.mark.parametrize("name,path,expected",
                         list(_cards()) or [pytest.param(None, None, None,
                                            marks=pytest.mark.skip("에이전트 설정이 없다"))])
class TestRealAgentCards:
    def test_side_effect_matches_pre_transition_behaviour(self, name, path, expected):
        card = load_card(path, url="http://localhost:1")
        assert side_effect_of(card) == expected, (
            f"{name} 의 병렬 거동이 바뀐다: {expected} → {side_effect_of(card)}")

    def test_every_skill_declares_hints(self, name, path, expected):
        card = load_card(path, url="http://localhost:1")
        ext = next(e for e in card.capabilities.extensions if "tool-hints" in e.uri)
        declared = set((ext.params or {}).get("skills") or {})
        assert declared == {s.id for s in card.skills}, f"{name}: 힌트 없는 스킬이 있다"

    def test_description_has_an_exclusion(self, name, path, expected):
        """라우팅의 유일한 근거다. '…는 이 에이전트가 아니다' 가 있어야 한다."""
        card = load_card(path, url="http://localhost:1")
        assert any(w in card.description for w in ("아니다", "않는다")), \
            f"{name}: description 에 경계가 없다"
