"""`resume_query` — 재개 발화를 후보에 대해 해석되게 감싸는 순수 함수.

답변만 넘기면 모델이 그것을 새 질의로 읽는다. `"파란 거"` 는 그 자체로 뜻이 없다 —
그게 실측에서 통과율 0% 를 만든 실패의 뿌리다.
"""

from executor import ResumeState, resume_query


def _state(**kw):
    base = dict(original="머그컵 재고 있어?", asked="어느 것입니까?",
                candidates=[{"sku": "A-1023", "label": "파란 머그컵"}])
    return ResumeState(**{**base, **kw})


class TestCarriesTheThreeMusts:
    def test_original_request(self):
        assert "머그컵 재고 있어?" in resume_query(_state(), "파란 거")

    def test_previous_question(self):
        assert "어느 것입니까?" in resume_query(_state(), "파란 거")

    def test_candidate_list(self):
        q = resume_query(_state(), "파란 거")
        assert "A-1023" in q and "파란 머그컵" in q

    def test_the_answer_itself(self):
        assert "파란 거" in resume_query(_state(), "파란 거")

    def test_is_not_the_bare_answer(self):
        assert resume_query(_state(), "파란 거") != "파란 거"


class TestInstructs:
    def test_forbids_reading_as_a_new_query(self):
        assert "새 질의로 읽지 말고" in resume_query(_state(), "답")

    def test_forbids_repeating_the_same_question(self):
        assert "같은 질문을 반복하지 말고" in resume_query(_state(), "답")


class TestDegrades:
    def test_without_a_question(self):
        q = resume_query(_state(asked=""), "답")
        assert "당신이 물었습니다" not in q and "머그컵 재고 있어?" in q

    def test_without_candidates(self):
        q = resume_query(_state(candidates=[]), "답")
        assert "제시한 후보" not in q and "답" in q

    def test_without_candidates_does_not_claim_a_candidate(self):
        # 후보 없이 물었는데 "후보 중 하나를 가리킨다·새 질의로 읽지 마라"고 하면, 답 대신 온 새 질문이
        # 원래 요청에 묻혀 버린다(실측 2026-10-20: "예시 줘봐" → 되묻기 → 새 질문이 무시되고 예시를 지어냄)
        q = resume_query(_state(candidates=[]), "수의계약은 금액이 얼마까지야?")
        assert "후보" not in q and "새 질의로 읽지 말고" not in q

    def test_without_candidates_allows_a_new_question(self):
        q = resume_query(_state(candidates=[]), "수의계약은 금액이 얼마까지야?")
        assert "새 질문" in q
