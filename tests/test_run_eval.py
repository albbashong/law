import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).parent.parent / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


def _case(**kw):
    return {"id": "T", "must_refs": [], **kw}


def _task(answer):
    return {"status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"name": "answer", "parts": [{"text": answer}]}]}


def test_hallucination_not_excused_by_longer_number():
    rows = [{"refs": [["시행령", "별표 17 제2호"]], "texts": ""}]
    r = run_eval.evaluate(_case(), _task("별표 1 에 따르면"), rows)
    assert r["halluc_candidates"] == ["별표 1"]


def test_recall_prefix_respects_boundary():
    rows = [{"refs": [["국토의 계획 및 이용에 관한 법률", "제76조의2"]], "texts": ""}]
    case = _case(must_refs=[{"law": "국토의 계획 및 이용에 관한 법률", "article": "제76조"}])
    assert run_eval.evaluate(case, _task(""), rows)["recall"] == 0
    rows = [{"refs": [["국토의 계획 및 이용에 관한 법률", "제76조제1항"]], "texts": ""}]
    assert run_eval.evaluate(case, _task(""), rows)["recall"] == 1


def test_must_mention_scores_the_share_of_phrases_in_the_answer():
    case = _case(must_mention=["공장설립", "개발행위 허가"])
    r = run_eval.evaluate(case, _task("면적이 크면 공장설립 승인이 필요해요"), [])
    assert r["mention"] == 0.5 and r["missing"] == ["개발행위 허가"]


def test_expect_prefix_checks_how_the_answer_starts():
    case = _case(expect_prefix="예를 들어")
    assert run_eval.evaluate(case, _task("  예를 들어 볼게요."), [])["prefix_ok"] is True
    assert run_eval.evaluate(case, _task("**요약** 예를 들어"), [])["prefix_ok"] is False
    assert run_eval.evaluate(_case(), _task("x"), [])["prefix_ok"] is None


def test_turns_of_a_case_default_to_its_utterance():
    assert run_eval.turns_of({"utterance": "a"}) == ["a"]
    assert run_eval.turns_of({"turns": ["a", "b"]}) == ["a", "b"]


def test_summary_per_case_gives_rates_over_repeats():
    rs = [{"id": "S1", "prefix_ok": True, "mention": 1.0, "recall": 1.0, "halluc_candidates": [], "state_ok": True},
          {"id": "S1", "prefix_ok": False, "mention": 0.5, "recall": 1.0, "halluc_candidates": ["제9조"],
           "state_ok": True}]
    s = run_eval.per_case(rs)["S1"]
    assert s == {"runs": 2, "prefix_rate": 0.5, "mention_avg": 0.75, "recall_avg": 1.0, "halluc_rate": 0.5,
                 "state_rate": 1.0}
