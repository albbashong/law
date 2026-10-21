"""이야기로 다시 쓰기 — 모델이 retell_as_story 를 부르면, 검증을 마친 사실 답을 별도 지시(STORY_PROMPT)로
한 번 더 불러 이야기로 바꾼다. 이야기도 인용 검증을 거치고, 걸리면 사실 답을 낸다."""
import loop
from config import settings


class FakeMcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "조문", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}]}}


class FakeModel:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append(([dict(m) for m in messages], [t["name"] for t in tools]))
        return self.script.pop(0)


STORY = {"tool": loop.STORY, "args": {}}
LOOKUP = {"tool": "law_article", "args": {"law": "법", "article": "제25조"}}


def _on(monkeypatch, tmp_path, text="이야기로 다시 쓰세요."):
    (tmp_path / "story.txt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "story_prompt", "story.txt")
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")


async def test_story_tool_is_offered_only_when_configured(monkeypatch, tmp_path):
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("q", mcp=FakeMcp(), model=model)
    assert loop.STORY not in model.seen[0][1]
    _on(monkeypatch, tmp_path)
    model = FakeModel([{"answer": "답"}])
    await loop.run_query("q", mcp=FakeMcp(), model=model)
    assert loop.STORY in model.seen[0][1]


async def test_verified_answer_is_retold_with_the_story_prompt_and_no_tools(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "예를 들어 볼게요. 제25조에 따르면 할 수 있어요."}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요. 제25조에 따르면 할 수 있어요."
    msgs, tools = model.seen[-1]
    assert tools == []
    assert msgs[0] == {"role": "system", "content": "이야기로 다시 쓰세요."}
    assert "예시 줘봐" in msgs[1]["content"] and "제25조가 정합니다." in msgs[1]["content"]


async def test_story_citing_what_was_not_looked_up_falls_back_to_the_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "예를 들어 볼게요. 제99조에 따르면 할 수 있어요."},
                       {"answer": "예를 들어 볼게요. 제98조에 따르면 할 수 있어요."}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다."


async def test_empty_story_falls_back_to_the_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "  "}, {"answer": ""}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다."


async def test_without_the_story_call_the_answer_is_not_retold(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = FakeModel([LOOKUP, {"answer": "제25조가 정합니다."}])
    raw = await loop.run_query("q", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다." and len(model.seen) == 2


async def test_story_step_sees_this_turns_lookup_results(monkeypatch, tmp_path):
    """요약된 답만으로는 호·목 구조(별개 사유인지 함께 충족할 요건인지)가 흐려진다 — 조회 원문도 함께 넘긴다."""
    _on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    request = model.seen[-1][0][1]["content"]
    assert '"article": "제25조"' in request                       # law_article 결과
    assert settings.story_ack_text not in request                 # 예약 도구의 답은 원문이 아니다


async def test_story_off_format_is_retried_then_used(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "story_format_pattern", r"^예를 들어")
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "| 표 | 제25조 |"}, {"answer": "예를 들어 볼게요. 제25조…"}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요. 제25조…"


async def test_story_off_format_every_time_falls_back_to_the_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "story_format_pattern", r"^예를 들어")
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "| 표 |"}, {"answer": "목록: 제25조"}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다." and len(model.seen) == 5


# ── 앞 답을 이야기로 — 런타임이 [이전 작업] 마지막 턴의 조회를 그대로 다시 실행한다 ─────────

class RecordingMcp(FakeMcp):
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [*await super().list_tools(), {"name": "building_basis", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"status": "ok", "facts": {"refs": [{"law": "조례", "article": "제33조"}]}}


BASIS = {"tool": "building_basis", "args": {"use": "공장", "region": "영천시"}}
MEM = {"memory": {"turns": [
    {"q": "옛 질문", "a": "옛 답", "calls": [{"tool": "law_article", "args": {"law": "x"}}]},
    {"q": "공장 지을 수 있어?", "a": "조례 제33조", "calls": [BASIS, {"tool": "nope", "args": {}}]},
]}}


async def test_story_replays_the_last_turns_lookups(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = RecordingMcp()
    model = FakeModel([STORY, {"answer": "조례 제33조가 정합니다."}, {"answer": "예를 들어 볼게요. 제33조…"}])
    raw = await loop.run_query("쉽게 설명해줘", mcp=mcp, model=model, context=MEM)
    assert mcp.calls == [("building_basis", BASIS["args"])]          # 마지막 턴만, 모르는 도구는 빼고
    assert raw["answer"] == "예를 들어 볼게요. 제33조…"               # 다시 실행한 결과가 인용 근거가 된다
    seen = model.seen[1][0]
    replay = [m for m in seen if m.get("role") == "tool" and m.get("name") == "building_basis"]
    assert replay and "제33조" in replay[0]["content"]
    asked = [m for m in seen if m.get("role") == "assistant" and m.get("tool_calls")]
    assert any(c["id"] == replay[0]["tool_call_id"] for m in asked for c in m["tool_calls"])
    assert {"tool": "building_basis", "args": BASIS["args"]} in raw["calls"]


async def test_replayed_lookup_counts_toward_repeats(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_retry_max", 1)
    mcp = RecordingMcp()
    model = FakeModel([STORY, BASIS, {"answer": "조례 제33조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    await loop.run_query("쉽게 설명해줘", mcp=mcp, model=model, context=MEM)
    assert len(mcp.calls) == 1


async def test_story_without_previous_work_replays_nothing(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = RecordingMcp()
    model = FakeModel([STORY, BASIS, {"answer": "조례 제33조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    await loop.run_query("쉽게 설명해줘", mcp=mcp, model=model)
    assert mcp.calls == [("building_basis", BASIS["args"])]


# ── 이야기 계획 — 쓸 사유와 그 관계를 먼저 고르게 하고, 인용한 값이 조회 원문에 있는지 대조한 뒤에 쓴다 ─────

class StructuredMcp(FakeMcp):
    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}],
                                          "구조": [{"조항": "제25조제1항", "관계": "각 호 중 하나만 해당하면 된다"}]}}


def _plan_on(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    (tmp_path / "plan.txt").write_text("계획을 JSON 으로.", encoding="utf-8")
    monkeypatch.setattr(settings, "story_plan_prompt", "plan.txt")
    monkeypatch.setattr(settings, "story_plan_grounded_keys", "관계")


GOOD_PLAN = {"answer": '계획: {"사유": "제25조제1항", "관계": "각 호 중 하나만 해당하면 된다", "대상": "다리"}'}


async def test_story_is_written_from_a_checked_plan(monkeypatch, tmp_path):
    _plan_on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, GOOD_PLAN, {"answer": "예를 들어 볼게요."}])
    raw = await loop.run_query("예시 줘봐", mcp=StructuredMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요."
    plan_msgs, plan_tools = model.seen[-2]
    assert plan_msgs[0]["content"] == "계획을 JSON 으로." and plan_tools == []
    story_request = model.seen[-1][0][1]["content"]
    assert "각 호 중 하나만 해당하면 된다" in story_request and '"대상": "다리"' in story_request


async def test_plan_quoting_what_the_lookups_do_not_say_is_retried(monkeypatch, tmp_path):
    _plan_on(monkeypatch, tmp_path)
    bad = {"answer": '{"사유": "제25조제1항", "관계": "각 호를 모두 충족해야 한다"}'}
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, bad, GOOD_PLAN, {"answer": "예를 들어 볼게요."}])
    raw = await loop.run_query("예시 줘봐", mcp=StructuredMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요."


async def test_no_checked_plan_falls_back_to_the_answer(monkeypatch, tmp_path):
    _plan_on(monkeypatch, tmp_path)
    bad = {"answer": '{"사유": "제25조제1항", "관계": "각 호를 모두 충족해야 한다"}'}
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, bad, {"answer": "계획 없음"}])
    raw = await loop.run_query("예시 줘봐", mcp=StructuredMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다." and len(model.seen) == 5


async def test_plan_may_leave_a_grounded_key_empty(monkeypatch, tmp_path):
    _plan_on(monkeypatch, tmp_path)
    plan = {"answer": '{"사유": "제25조제1항", "관계": null}'}
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, plan, {"answer": "예를 들어 볼게요."}])
    raw = await loop.run_query("예시 줘봐", mcp=StructuredMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요."


async def test_plan_is_skipped_when_the_lookups_carry_no_grounded_key(monkeypatch, tmp_path):
    """대조할 값(예: 구조의 관계)이 조회 결과에 없으면 계획이 지킬 기준도 없다 — 바로 이야기를 쓴다."""
    _plan_on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요." and len(model.seen) == 4


# ── 설명틀 — 조문 생김새에 맞는 쓰는 법을 골라 끼우고, 조회한 법령명을 지시문 빈칸에 채운다 ─────────────

def _forms_on(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, "당신은 {법령}을 풀어 주는 담당입니다. 유형: {설명틀}\n{설명틀_쓰는법}")
    forms = tmp_path / "forms"
    forms.mkdir()
    (forms / "목록형.txt").write_text("허용·금지 목록에서 찾는 조문\n주인공이 목록에서 찾는다.", encoding="utf-8")
    (forms / "택일형.txt").write_text("어느 하나에 해당하면 되는 조문\n한 길로 통과한다.", encoding="utf-8")
    monkeypatch.setattr(settings, "story_forms_dir", "forms")
    monkeypatch.setattr(settings, "story_law_keys", "law")


async def test_story_tool_offers_the_forms_on_disk(monkeypatch, tmp_path):
    _forms_on(monkeypatch, tmp_path)
    model = FakeModel([{"answer": "답"}])
    seen = []
    orig = model.next_step

    async def spy(messages, tools):
        seen.append(tools)
        return await orig(messages, tools)
    model.next_step = spy
    await loop.run_query("q", mcp=FakeMcp(), model=model)
    story = next(t for t in seen[0] if t["name"] == loop.STORY)
    form = story["input_schema"]["properties"]["form"]
    assert form["enum"] == ["목록형", "택일형"] and "어느 하나에 해당하면 되는 조문" in form["description"]


async def test_chosen_form_and_looked_up_laws_fill_the_story_prompt(monkeypatch, tmp_path):
    _forms_on(monkeypatch, tmp_path)
    story = {"tool": loop.STORY, "args": {"form": "택일형"}}
    model = FakeModel([story, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    system = model.seen[-1][0][0]["content"]
    assert system.startswith("당신은 법을 풀어 주는 담당입니다. 유형: 택일형")
    assert "한 길로 통과한다." in system and "어느 하나에 해당하면" not in system   # 첫 줄은 고르는 데만 쓴다


async def test_unknown_form_leaves_the_general_prompt(monkeypatch, tmp_path):
    _forms_on(monkeypatch, tmp_path)
    story = {"tool": loop.STORY, "args": {"form": "없는틀"}}
    model = FakeModel([story, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "예를 들어 볼게요."}])
    await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    system = model.seen[-1][0][0]["content"]
    assert "{설명틀" not in system and "{법령}" not in system and "주인공" not in system


# ── 설명틀 고르기·이야기 점검 — 검증된 답을 본 뒤에 틀을 고르고, 쓴 이야기를 답과 대조해 고칠 점이 있으면 다시 쓴다 ──

def _select_on(monkeypatch, tmp_path):
    _forms_on(monkeypatch, tmp_path)
    (tmp_path / "select.txt").write_text("틀 하나만 답하세요.\n{설명틀_목록}", encoding="utf-8")
    monkeypatch.setattr(settings, "story_form_select_prompt", "select.txt")


async def test_form_is_chosen_after_the_answer_is_verified(monkeypatch, tmp_path):
    _select_on(monkeypatch, tmp_path)
    story = {"tool": loop.STORY, "args": {"form": "목록형"}}            # 조회 전에 고른 틀
    model = FakeModel([story, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "택일형"},
                       {"answer": "예를 들어 볼게요."}])
    await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    select_msgs, _ = model.seen[-2]
    assert "어느 하나에 해당하면 되는 조문" in select_msgs[0]["content"]
    assert "제25조가 정합니다." in select_msgs[1]["content"]
    assert "유형: 택일형" in model.seen[-1][0][0]["content"]


async def test_unusable_selection_keeps_the_tool_choice(monkeypatch, tmp_path):
    _select_on(monkeypatch, tmp_path)
    story = {"tool": loop.STORY, "args": {"form": "목록형"}}
    model = FakeModel([story, LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": "모르겠어요"},
                       {"answer": "예를 들어 볼게요."}])
    await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert "유형: 목록형" in model.seen[-1][0][0]["content"]


def _review_on(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    (tmp_path / "review.txt").write_text("이야기를 점검해 JSON 으로.", encoding="utf-8")
    monkeypatch.setattr(settings, "story_review_prompt", "review.txt")


async def test_story_with_review_problems_is_rewritten_with_them(monkeypatch, tmp_path):
    _review_on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "예를 들어 볼게요. 자유롭게 해도 돼요."},
                       {"answer": '{"문제": ["\'자유롭게\'는 답보다 강한 결론"]}'},
                       {"answer": "예를 들어 볼게요. 이 조문의 대상은 아니에요."},
                       {"answer": '{"문제": []}'}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요. 이 조문의 대상은 아니에요."
    rewrite_request = model.seen[-2][0][1]["content"]
    assert "답보다 강한 결론" in rewrite_request
    review_msgs, review_tools = model.seen[-1]
    assert review_msgs[0]["content"] == "이야기를 점검해 JSON 으로." and review_tools == []
    assert "이 조문의 대상은 아니에요" in review_msgs[1]["content"]


async def test_story_still_flagged_after_rewrite_falls_back_to_the_answer(monkeypatch, tmp_path):
    _review_on(monkeypatch, tmp_path)
    flagged = {"answer": '{"문제": ["지어낸 절차"]}'}
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "예를 들어 볼게요. 1"}, flagged, {"answer": "예를 들어 볼게요. 2"}, flagged])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다."


async def test_unreadable_review_passes_the_story(monkeypatch, tmp_path):
    _review_on(monkeypatch, tmp_path)
    model = FakeModel([STORY, LOOKUP, {"answer": "제25조가 정합니다."},
                       {"answer": "예를 들어 볼게요."}, {"answer": "잘 썼네요"}])
    raw = await loop.run_query("예시 줘봐", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "예를 들어 볼게요."
