"""run_eval 의 순수 함수 — 호출자 거르기 · 근거 통과 · 계획 준수."""
import run_eval as ev


def _task(artifacts):
    return {"status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": artifacts}


def _art(name, data):
    return {"name": name, "parts": [{"data": data}]}


def test_rows_from_ignored_caller_are_dropped():
    rows = [{"tool": "a", "caller": "search_plan"}, {"tool": "b", "caller": ""}, {"tool": "c"}]
    assert [r["tool"] for r in ev.keep_rows(rows, ignore="search_plan")] == ["b", "c"]


def test_only_caller_keeps_that_callers_rows():
    rows = [{"tool": "a", "caller": "small4b"}, {"tool": "b", "caller": ""}, {"tool": "c", "caller": "search_plan"}]
    assert [r["tool"] for r in ev.keep_rows(rows, ignore="search_plan", only="small4b")] == ["a"]


def test_grounded_ok_reads_verification():
    assert ev.grounded_ok(_task([])) is True
    assert ev.grounded_ok(_task([_art("verification", {"lookups": 2, "unverified": []})])) is True
    assert ev.grounded_ok(_task([_art("verification", {"lookups": 2, "unverified": ["제9조"]})])) is False


def test_plan_fields_come_from_facts():
    plan = {"단계": [{"꼭_열기": True}, {"꼭_열기": True}, {"꼭_열기": False}]}
    t = _task([_art("facts", {"검색_계획": plan, "계획_미준수": ["A법 제1조"]})])
    assert ev.plan_of(t, "검색_계획") == {"plan_steps": 3, "plan_must": 2, "plan_missed": ["A법 제1조"]}
    assert ev.plan_of(_task([]), "검색_계획") == {"plan_steps": 0, "plan_must": 0, "plan_missed": []}


def test_plan_follow_rate():
    rs = [{"plan_must": 2, "plan_missed": ["x"]}, {"plan_must": 2, "plan_missed": []}, {"plan_must": 0, "plan_missed": []}]
    assert ev.plan_follow_rate(rs) == 0.75
    assert ev.plan_follow_rate([{"plan_must": 0, "plan_missed": []}]) is None
