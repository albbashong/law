"""검색 계획 — PLAN_AGENT_URL 이 있으면 질문마다 계획을 받아 질문 뒤에 붙이고, 계획의 꼭 열 조문을 열지 않은 답은
그 조문을 짚어 한 번 되돌린다. 계획이 없으면(꺼짐·실패) 지금과 똑같이 동작한다."""
import json

import httpx
import pytest

import loop
import plan as planmod
from config import settings

CITE = r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?"
RETRY = "계획의 꼭 열 조문을 열지 않았습니다: {steps}. law_article 로 열어 확인하고 다시 답하세요."
PLAN = {"단계": [{"law": "국토의 계획 및 이용에 관한 법률", "article": "제77조", "관계": "시작", "꼭_열기": True, "이어진": None},
                {"law": "건축법", "article": "제2조", "관계": "인용", "꼭_열기": False, "이어진": 0}],
        "멈춤": [], "지역": None}


class Mcp:
    def __init__(self, result=None):
        self.result = result or {"status": "ok", "facts": {"법령": "국토의 계획 및 이용에 관한 법률", "조문": "제77조", "원문": "건폐율"}}

    async def list_tools(self):
        return [{"name": "law_article", "description": "조문을 연다.", "input_schema": {}}]

    async def call(self, name, args):
        return self.result


class Model:
    def __init__(self, script):
        self.script, self.seen, self.required = list(script), [], []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        self.required.append(require)
        return self.script.pop(0)


OPEN = {"tools": [{"id": "c1", "name": "law_article", "args": {"law": "국토계획법", "article": "제77조"}}]}


@pytest.fixture(autouse=True)
def _texts(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    monkeypatch.setattr(settings, "plan_intro_text", "검색 계획(이 순서로 여세요):")
    monkeypatch.setattr(settings, "plan_step_format", "{n}. {law} {article} ({relation}){must}")
    monkeypatch.setattr(settings, "plan_must_text", " — 꼭 열기")
    monkeypatch.setattr(settings, "plan_stop_text", "멈춘 곳: {stops}")
    monkeypatch.setattr(settings, "plan_retry_text", RETRY)


async def _planner(q):
    return PLAN


async def test_plan_is_shown_after_the_first_lookup():
    """계획은 첫 조회 결과 뒤에 붙인다 — 처음부터 보이면 모델이 전용 도구(building_basis·law_definition)를 건너뛰고
    계획의 조문만 연다(실측 2026-11-16 120b G06·G17·G29 재현율 1.0→0)."""
    model = Model([OPEN, {"answer": "답입니다."}])
    await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert not [m for m in model.seen[0] if m.get("name") == planmod.PLAN_MESSAGE_NAME]
    shown = [m for m in model.seen[1] if m.get("name") == planmod.PLAN_MESSAGE_NAME]
    assert shown and "국토의 계획 및 이용에 관한 법률 제77조" in shown[0]["content"] and "꼭 열기" in shown[0]["content"]
    assert model.seen[1].index(shown[0]) > [i for i, m in enumerate(model.seen[1]) if m["role"] == "tool"][0]


LAW_ANSWER = {"answer": "국토의 계획 및 이용에 관한 법률에 따라 답입니다."}


async def test_unopened_must_step_is_sent_back():
    model = Model([LAW_ANSWER, OPEN, {"answer": "제77조에 따라 답입니다."}])
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert model.seen[1][-1]["content"].startswith("계획의 꼭 열 조문을 열지 않았습니다: 국토의 계획 및 이용에 관한 법률 제77조")
    assert model.required[1] is True
    assert raw["answer"] == "제77조에 따라 답입니다."


async def test_step_seen_in_chain_excerpt_counts_as_opened():
    mcp = Mcp({"status": "ok", "facts": {"법령": "건축법", "조문": "제55조",
                                        "refs": [{"law": "국토의 계획 및 이용에 관한 법률", "article": "제77조", "excerpt": "건폐율"}]}})
    model = Model([OPEN, {"answer": "답입니다."}])
    raw = await loop.run_query("건폐율은?", mcp=mcp, model=model, planner=_planner)
    assert len(model.seen) == 2 and raw["answer"] == "답입니다."


async def test_step_only_in_pointer_list_is_not_opened():
    mcp = Mcp({"status": "ok", "pointer": ["핵심_조문"],
               "facts": {"핵심_조문": ["국토의 계획 및 이용에 관한 법률 제77조"]}})
    model = Model([OPEN, LAW_ANSWER, LAW_ANSWER])
    await loop.run_query("건폐율은?", mcp=mcp, model=model, planner=_planner)
    assert model.seen[2][-1]["content"].startswith("계획의 꼭 열 조문을 열지 않았습니다")


async def test_plan_agent_down_answers_as_before():
    async def down(q):
        return None
    with_none, without = Model([{"answer": "답입니다."}]), Model([{"answer": "답입니다."}])
    a = await loop.run_query("건폐율은?", mcp=Mcp(), model=with_none, planner=down)
    b = await loop.run_query("건폐율은?", mcp=Mcp(), model=without)
    assert with_none.seen == without.seen and a["answer"] == b["answer"]


async def test_no_plan_url_never_calls_planner(monkeypatch):
    monkeypatch.setattr(settings, "plan_agent_url", "")

    async def boom(*a, **k):
        raise AssertionError("불리면 안 됨")
    monkeypatch.setattr(planmod, "fetch_plan", boom)
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=Model([{"answer": "답입니다."}]))
    assert raw["answer"] == "답입니다."


async def test_resume_does_not_fetch_plan_again():
    called = []

    async def planner(q):
        called.append(q)
        return PLAN
    prior = {"messages": [{"role": "user", "content": "앞 질문"}, {"role": "assistant", "content": "어느 지역인가요?"}]}
    await loop.run_query("영천시", mcp=Mcp(), model=Model([{"answer": "답입니다."}, {"answer": "답입니다."}]),
                         planner=planner, prior=prior)
    assert called == []


async def test_plan_is_reported_in_facts_with_misses():
    model = Model([{"answer": "답입니다."}])
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    facts = raw["tool_results"][settings.plan_key]["facts"]
    assert facts[settings.plan_key] == PLAN
    assert facts["계획_미준수"] == ["국토의 계획 및 이용에 관한 법률 제77조"]
    assert "ungrounded" not in raw or "국토의 계획 및 이용에 관한 법률 제77조" not in (raw.get("ungrounded") or [])


def test_unopened_matches_law_and_article_in_one_tool_message():
    msgs = [{"role": "tool", "content": json.dumps({"facts": {"법령": "건축법", "조문": "제77조"}}, ensure_ascii=False)},
            {"role": "tool", "content": json.dumps({"facts": {"법령": "국토의 계획 및 이용에 관한 법률"}}, ensure_ascii=False)}]
    assert planmod.unopened(PLAN, msgs, set()) == ["국토의 계획 및 이용에 관한 법률 제77조"]


async def test_fetch_plan_reads_the_facts_artifact(monkeypatch):
    task = {"task": {"id": "t", "status": {"state": "TASK_STATE_COMPLETED"},
                     "artifacts": [{"name": "facts", "parts": [{"data": {settings.plan_key: PLAN}}]}]}}

    def handler(req):
        assert req.url.path.endswith("/message:send")
        return httpx.Response(200, json=task)
    monkeypatch.setattr(planmod, "_transport", httpx.MockTransport(handler))
    assert await planmod.fetch_plan("http://plan", "질문", 5) == PLAN


async def test_fetch_plan_failure_is_none(monkeypatch):
    monkeypatch.setattr(planmod, "_transport", httpx.MockTransport(lambda req: httpx.Response(500)))
    assert await planmod.fetch_plan("http://plan", "질문", 5) is None


async def test_fetch_plan_empty_steps_is_none(monkeypatch):
    task = {"task": {"artifacts": [{"name": "facts", "parts": [{"data": {settings.plan_key: {"단계": []}}}]}]}}
    monkeypatch.setattr(planmod, "_transport", httpx.MockTransport(lambda req: httpx.Response(200, json=task)))
    assert await planmod.fetch_plan("http://plan", "질문", 5) is None


def _restate_on(monkeypatch, tmp_path):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")


async def test_plan_is_asked_with_the_restated_question(monkeypatch, tmp_path):
    """계획은 정리된 질문으로 받는다 — 말투 섞인 원문으로 검색하면 엉뚱한 조례가 시작 조문이 된다(실측 2026-11-16)."""
    _restate_on(monkeypatch, tmp_path)
    asked = []

    async def planner(q):
        asked.append(q)
        return PLAN
    model = Model([{"answer": '{"질문": "농장 건축 절차", "짐작": ""}'}, OPEN, {"answer": "제77조입니다."}])
    await loop.run_query("그러면 내가 농장 짓는 경우 예시좀 들어줄래", mcp=Mcp(), model=model, planner=planner)
    assert asked == ["농장 건축 절차"]


async def test_follow_up_without_a_restated_question_gets_no_plan():
    """이어지는 질문인데 정리된 질문이 없으면 계획을 받지 않는다 — 원문만으로는 대상(앞 질문의 필지 등)을 모른다."""
    asked = []

    async def planner(q):
        asked.append(q)
        return PLAN
    context = {settings.memory_key: {"turns": [{"q": "영천시 1-51에 공장 돼?", "a": "조건부", "calls": []}]}}
    await loop.run_query("그럼 농장은?", mcp=Mcp(), model=Model([{"answer": "답입니다."}]), planner=planner,
                         context=context)
    assert asked == []


async def test_plan_lines_are_not_grounds_for_cites():
    """계획에 적힌 조문 번호는 연 것이 아니다 — 조회 없이 인용하면 되돌린다(리뷰 2026-11-16 Critical)."""
    model = Model([{"answer": "건축법 제2조에 따릅니다."}, {"answer": "건축법 제2조에 따릅니다."}])
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert "제2조" in (raw.get("ungrounded") or [])


async def test_plan_lines_carried_into_a_resumed_turn_are_not_grounds():
    first = await loop.run_query("건폐율은?", mcp=Mcp(), model=Model([OPEN, {"answer": "제77조입니다."}]), planner=_planner)
    model = Model([{"answer": "건축법 제2조에 따릅니다."}, {"answer": "건축법 제2조에 따릅니다."}])
    raw = await loop.run_query("그럼?", mcp=Mcp(), model=model, planner=_planner,
                               prior={"messages": first["messages"], "tool_results": first["tool_results"]})
    assert "제2조" in (raw.get("ungrounded") or [])


async def test_plan_retry_does_not_use_up_the_cite_retry():
    """계획 되돌림 뒤에도 조회 안 한 인용은 따로 한 번 되돌린다(리뷰 Important 3)."""
    model = Model([LAW_ANSWER, {"answer": "제99조에 따릅니다."}, {"answer": "제99조에 따릅니다."}])
    await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert len(model.seen) == 3 and "제99조" in model.seen[2][-1]["content"]


def test_unopened_does_not_take_a_branch_article_number():
    msgs = [{"role": "tool", "content": json.dumps({"facts": {"법령": "국토의 계획 및 이용에 관한 법률", "조문": "제77조의2"}},
                                                  ensure_ascii=False)}]
    assert planmod.unopened(PLAN, msgs, set()) == ["국토의 계획 및 이용에 관한 법률 제77조"]


def test_unopened_does_not_take_the_enforcement_decree_for_the_act():
    msgs = [{"role": "tool", "content": json.dumps({"facts": {"법령": "국토의 계획 및 이용에 관한 법률 시행령", "조문": "제77조"}},
                                                  ensure_ascii=False)}]
    plan = {"단계": [*PLAN["단계"], {"law": "국토의 계획 및 이용에 관한 법률 시행령", "article": "제84조", "관계": "하위",
                                    "꼭_열기": False, "이어진": 0}]}
    assert planmod.unopened(plan, msgs, set()) == ["국토의 계획 및 이용에 관한 법률 제77조"]


def test_unopened_needs_law_and_article_close_together():
    far = {"facts": {"법령": "국토의 계획 및 이용에 관한 법률", "원문": "가" * 400, "refs": [{"law": "건축법", "article": "제77조"}]}}
    msgs = [{"role": "tool", "content": json.dumps(far, ensure_ascii=False)}]
    assert planmod.unopened(PLAN, msgs, set()) == ["국토의 계획 및 이용에 관한 법률 제77조"]


async def test_plan_miss_on_a_law_the_answer_does_not_cover_is_not_sent_back():
    """답이 다루지 않는 법령의 꼭 열 조문은 되돌리지 않는다 — 엇나간 시작 조문의 계획이 되돌림 한 바퀴를 더 쓰게 한다
    (실측 2026-11-16 120b G01 산업집적법 제16조). 놓친 것은 facts 에는 그대로 남긴다."""
    model = Model([{"answer": "건축법에 따라 답입니다."}])
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert len(model.seen) == 1 and raw["answer"].startswith("건축법에 따라 답입니다.")
    assert raw["tool_results"][settings.plan_key]["facts"]["계획_미준수"] == ["국토의 계획 및 이용에 관한 법률 제77조"]


LIST_RETRY = "근거 표에 위임 단계가 빠졌습니다: {steps}. 근거 표에 한 줄씩 적고 다시 답하세요."


def test_unlisted_is_an_opened_must_step_missing_from_an_answer_on_its_law(monkeypatch):
    """연 꼭 열 조문인데, 답이 그 법령을 다루면서 그 조문을 적지 않았다 — 근거 표의 위임 단계가 빠진 것."""
    monkeypatch.setattr(settings, "plan_open_window", 80)
    law = "국토의 계획 및 이용에 관한 법률"
    assert planmod.unlisted(PLAN, f"「{law}」에 따라 답합니다.", []) == [f"{law} 제77조"]
    assert planmod.unlisted(PLAN, f"| {law} | 제77조 | 건폐율 |", []) == []
    assert planmod.unlisted(PLAN, "건축법에 따라 답합니다.", []) == []            # 그 법령을 다루지 않는 답
    assert planmod.unlisted(PLAN, f"「{law}」에 따라 답합니다.", [f"{law} 제77조"]) == []   # 안 연 것은 계획 되돌림이 맡는다


async def test_opened_step_left_out_of_the_answer_is_sent_back(monkeypatch):
    monkeypatch.setattr(settings, "plan_list_retry_text", LIST_RETRY)
    model = Model([OPEN, LAW_ANSWER, {"answer": "국토의 계획 및 이용에 관한 법률 제77조에 따릅니다."}])
    raw = await loop.run_query("건폐율은?", mcp=Mcp(), model=model, planner=_planner)
    assert model.seen[2][-1]["content"].startswith("근거 표에 위임 단계가 빠졌습니다: 국토의 계획 및 이용에 관한 법률 제77조")
    assert raw["answer"].startswith("국토의 계획 및 이용에 관한 법률 제77조에 따릅니다.")
