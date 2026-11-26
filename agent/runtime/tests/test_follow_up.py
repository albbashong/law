"""이어지는 질문 — 질문 정리 단계가 앞 질문을 함께 보고, 발화가 앞 질문의 대상(주소·지을 것)을 생략한 이어짐이라고 보면
(RESTATE_FOLLOW_KEY 칸이 참) 앞 턴의 조회를 런타임이 먼저 다시 실행해 근거를 놓는다.
실측 2026-10-28 파주 아파트: "그럼 내가 어떻게 진행해야해"가 "어떻게 진행해야 하는가"로만 정리되어, 앞 턴에서 찾은
주택법 사업계획승인을 버리고 건축법 사전결정부터 새로 찾았다."""
import loop
from config import settings


class Mcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "building_basis", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        return {"status": "ok", "facts": {"추가검토": ["사업계획의 승인(주택법 제15조)"]}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


PREV = {"tool": "building_basis", "args": {"use": "아파트", "address": "파주시 금신초교길 53"}}
CONTEXT = {"memory": {"turns": [{"q": "파주시 금신초교길 53에 아파트 지으려면?", "a": "주택법 제15조…", "calls": [PREV]}]}}


def _on(monkeypatch, tmp_path, key="이어짐"):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "restate_follow_key", key)


async def test_restatement_sees_the_previous_question(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"answer": "주택법 제15조의 사업계획승인부터 받습니다."}])
    await loop.run_query("그럼 내가 어떻게 진행해야해", mcp=Mcp(), model=model, context=CONTEXT)
    assert "파주시 금신초교길 53에 아파트 지으려면?" in model.seen[0][1]["content"]
    # 앞 질문에 있던 번지(53)를 채운 정리 질문은 버리지 않는다
    assert "파주시 금신초교길 53 아파트 건축 진행 절차" in model.seen[1][-1]["content"] or any(
        "파주시 금신초교길 53 아파트 건축 진행 절차" in str(m.get("content")) for m in model.seen[1])


async def test_follow_up_replays_the_previous_lookups_first(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"answer": "주택법 제15조의 사업계획승인부터 받습니다."}])
    raw = await loop.run_query("그럼 내가 어떻게 진행해야해", mcp=mcp, model=model, context=CONTEXT)
    assert mcp.calls == [("building_basis", PREV["args"])]
    assert any(m.get("role") == "tool" and "주택법 제15조" in m["content"] for m in model.seen[1])
    assert raw["answer"].startswith("주택법 제15조")


async def test_new_question_in_the_same_conversation_does_not_replay(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "청탁금지법 음식물 가액", "짐작": "", "이어짐": false}'}, {"answer": "답"}])
    await loop.run_query("청탁금지법 음식물 한도는?", mcp=mcp, model=model, context=CONTEXT)
    assert mcp.calls == []


async def test_no_replay_without_the_key(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, key="")
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "진행 절차", "짐작": "", "이어짐": true}'}, {"answer": "답"}])
    await loop.run_query("그럼 어떻게 해", mcp=mcp, model=model, context=CONTEXT)
    assert mcp.calls == []


async def test_address_parts_are_not_checked_as_terms(monkeypatch, tmp_path):
    # 실측 2026-10-29: "다문리 160 최소분할면적"에서 '다문리'·'160'을 법령 용어로 확인하느라 두 번 조회했다 —
    # 숫자가 든 말과, 발화에서 바로 뒤에 숫자(번지)가 오는 말은 주소의 일부라 확인하지 않는다(낱말 목록 없이 모양으로)
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "다문리 160 최소분할면적", "짐작": "", "확인할 말": ["다문리", "160", "254-3번지", "필지분할"]}'}])
    _, terms, *_ = await loop.restated(model, "다문리 160 필지분할 최소면적은? 용천리 254-3번지도", {"tokens_in": 0, "tokens_out": 0})
    assert terms == ["필지분할"]


async def test_alias_followed_by_an_amount_is_still_checked(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "q", "짐작": "", "확인할 말": ["김영란법"]}'}])
    _, terms, *_ = await loop.restated(model, "김영란법 3만원 넘으면?", {"tokens_in": 0, "tokens_out": 0})
    assert terms == ["김영란법"]


async def test_terms_from_the_previous_question_are_not_checked(monkeypatch, tmp_path):
    # 실측 2026-11-19 pg: 앞 질문의 '보도블럭'·'항의'가 농지 질문의 확인할 말로 남아 term_link 를 불렀다 — 발화가 아니라
    # 앞 질문 줄까지 붙인 정리 입력에서 글자를 찾아서였다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "restate_terms_key", "확인할 말")
    monkeypatch.setattr(settings, "restate_term_tool", "building_basis")
    monkeypatch.setattr(settings, "restate_term_arg", "word")
    mcp = Mcp()
    ctx = {"memory": {"turns": [{"q": "보도블럭에 누워서 항의하면 처벌받아?", "a": "…", "calls": []}]}}
    model = Model([{"answer": '{"질문": "농지에 공장을 지으면 위반인가", "짐작": "", "확인할 말": ["보도블럭", "항의", "농지"], "이어짐": false}'},
                   {"answer": "답"}])
    await loop.run_query("농지에 공장 지으면 위반이야?", mcp=mcp, model=model, context=ctx)
    assert [a["word"] for n, a in mcp.calls] == ["농지"]


# 실측 2026-11-19 pg: "얼마를 내야하는건지 계산좀"을 정리 단계가 앞 질문의 "500만원"을 채워 정리하고도 이어짐을 거짓으로
# 냈다 — 앞 질문에서 빌려 온 대상이 정리 질문에 있으면 이어지는 질문이다
TAX = {"tool": "law_article", "args": {"law": "소득세법", "article": "제104조"}}
FARM = {"tool": "building_basis", "args": {"use": "공장", "land_category": "농지"}}


async def test_new_question_does_not_carry_the_earlier_work(monkeypatch, tmp_path):
    # 정리 단계가 새 질문으로 보고 대상도 빌려 오지 않았다 — 앞 대화의 조회(공장·농지)를 보이면 모델이 그 인자를 베껴 부른다
    _on(monkeypatch, tmp_path)
    ctx = {"memory": {"turns": [{"q": "농지에 공장 지으면?", "a": "…", "calls": [FARM]}]}}
    model = Model([{"answer": '{"질문": "해외주식 양도차익 세금", "짐작": "", "이어짐": false}'}, {"answer": "답"}])
    raw = await loop.run_query("해외주식으로 번 돈 세금은?", mcp=Mcp(), model=model, context=ctx)
    assert not any("농지에 공장" in str(m.get("content")) for m in model.seen[1])
    assert raw["topic_start"] is True


async def test_follow_up_sees_only_turns_since_its_subject_started(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = Mcp()
    ctx = {"memory": {"turns": [{"q": "농지에 공장 지으면?", "a": "…", "calls": [FARM]},
                                {"q": "해외주식으로 250만원 넘게 벌면?", "a": "…", "calls": [TAX], "topic": True},
                                {"q": "500만원이면?", "a": "…", "calls": [TAX]}]}}
    model = Model([{"answer": '{"질문": "해외주식 500만원 세금 계산", "짐작": "", "이어짐": true}'}, {"answer": "답"}])
    raw = await loop.run_query("계산좀", mcp=mcp, model=model, context=ctx)
    assert not any("농지에 공장" in str(m.get("content")) for m in model.seen[1])
    assert any("250만원 넘게" in str(m.get("content")) for m in model.seen[1])
    assert raw["topic_start"] is False


async def test_restatement_sees_every_question_of_the_subject(monkeypatch, tmp_path):
    # 실측 2026-11-19: 바로 앞 질문("500만원이면?")만 보여 정리 질문에서 주제(해외주식)가 빠졌다
    _on(monkeypatch, tmp_path)
    ctx = {"memory": {"turns": [{"q": "농지에 공장 지으면?", "a": "…", "calls": [FARM]},
                                {"q": "해외주식으로 250만원 넘게 벌면?", "a": "…", "calls": [TAX], "topic": True},
                                {"q": "500만원이면?", "a": "…", "calls": [TAX]}]}}
    model = Model([{"answer": '{"질문": "해외주식 500만원 세금 계산", "짐작": "", "이어짐": true}'}, {"answer": "답"}])
    await loop.run_query("계산좀", mcp=Mcp(), model=model, context=ctx)
    sent = model.seen[0][1]["content"]
    assert "해외주식으로 250만원 넘게 벌면?" in sent and "500만원이면?" in sent and "농지에 공장" not in sent


# 이어짐을 참/거짓 판단이 아니라 '앞 질문에서 가져온 말' 목록으로 받는다 — 실측 2026-11-20 qwen3:8b: 앞 질문의 대상을
# 채워 정리하고도 이어짐을 거짓으로 낸 것이 18번 중 2번, 가져온 말 목록은 18번 모두 맞았다.
TAX = {"tool": "building_basis", "args": {"use": "해외주식"}}
TAX_CTX = {"memory": {"turns": [{"q": "해외주식으로 500만원벌었을때 예시좀", "a": "…", "calls": [TAX]}]}}


async def test_words_taken_from_the_previous_question_make_a_follow_up(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, key="가져온 말")
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "해외주식으로 500만원 벌었을 때 세금 계산", "짐작": "", "가져온 말": ["해외주식", "500만원"]}'},
                   {"answer": "답"}])
    raw = await loop.run_query("얼마를 내야하는건지 계산좀", mcp=mcp, model=model, context=TAX_CTX)
    assert mcp.calls == [("building_basis", TAX["args"])]
    assert raw["topic_start"] is False


async def test_taken_words_not_in_the_previous_question_do_not_make_a_follow_up(monkeypatch, tmp_path):
    # 앞 질문에 없는 말(지어낸 것)이나 이번 발화에 이미 있는 말은 가져온 것이 아니다
    _on(monkeypatch, tmp_path, key="가져온 말")
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "농지에 창고를 지어도 되는가", "짐작": "", "가져온 말": ["양도소득", "농지"]}'},
                   {"answer": "답"}])
    raw = await loop.run_query("농지에 창고 지어도 돼?", mcp=mcp, model=model, context=TAX_CTX)
    assert mcp.calls == []
    assert raw["topic_start"] is True


async def test_empty_taken_words_start_a_new_subject(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, key="가져온 말")
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "청탁금지법 음식물 가액 한도", "짐작": "", "가져온 말": []}'}, {"answer": "답"}])
    raw = await loop.run_query("청탁금지법 음식물 가액 한도는?", mcp=mcp, model=model, context=TAX_CTX)
    assert mcp.calls == [] and raw["topic_start"] is True


# ── 다시 실행한 조회도 본 루프의 조회와 같은 절차로 등록한다 (전수조사 S2) ─────────────────────
class FooterMcp(Mcp):
    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        return {"status": "ok", "facts": {"추가검토": ["사업계획의 승인(주택법 제15조)"]},
                settings.footer_key: ["출처: 브이월드 토지이용계획"]}


async def test_replayed_lookup_registers_its_footer_like_a_fresh_one(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"answer": "주택법 제15조의 사업계획승인부터 받습니다."}])
    raw = await loop.run_query("그럼 내가 어떻게 진행해야해", mcp=FooterMcp(), model=model, context=CONTEXT)
    assert "출처: 브이월드 토지이용계획" in raw["answer"]


class AskMcp(Mcp):
    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        return {"status": "needs_disambiguation",
                "facts": {"candidates": [{"id": "1", "label": "경기 광주시"}, {"id": "2", "label": "광주광역시"}]}}


async def test_replayed_lookup_that_needs_a_choice_is_asked_not_answered(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "disambiguation_status", "needs_disambiguation")
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"answer": "어느 광주인지 알려 주세요."}])
    raw = await loop.run_query("그럼 내가 어떻게 진행해야해", mcp=AskMcp(), model=model, context=CONTEXT)
    assert raw["stopped"] == "ask"


async def test_previous_lookups_are_replayed_once_even_when_a_story_is_asked(monkeypatch, tmp_path):
    # 이어지는 질문이라 앞 턴 조회를 이미 다시 실행했다 — 모델이 이야기 도구를 불러도 같은 조회를 또 실행하지 않는다(S11)
    _on(monkeypatch, tmp_path)
    (tmp_path / "story.txt").write_text("이야기로 다시 쓰세요.", encoding="utf-8")
    monkeypatch.setattr(settings, "story_prompt", "story.txt")
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"tools": [{"id": "s1", "name": loop.STORY, "args": {}}]},
                   {"answer": "주택법 제15조의 사업계획승인부터 받습니다."},
                   {"answer": "예를 들어 주택법 제15조의 승인을 먼저 받아요."}])
    await loop.run_query("그럼 예시로 설명해줘", mcp=mcp, model=model, context=CONTEXT)
    assert mcp.calls == [("building_basis", PREV["args"])]


async def test_replayed_lookups_do_not_spend_the_no_new_evidence_budget(monkeypatch, tmp_path):
    # 앞 턴이 같은 조문을 두 번 연 대화 — 다시 실행만으로 '같은 결과 반복' 셈이 차서 모델의 첫 조회가 막히면 안 된다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    ctx = {"memory": {"turns": [{"q": "파주시 금신초교길 53에 아파트 지으려면?", "a": "…", "calls": [
        PREV, {"tool": "building_basis", "args": {"use": "아파트"}}]}]}}
    model = Model([{"answer": '{"질문": "파주시 금신초교길 53 아파트 건축 진행 절차", "짐작": "", "이어짐": true}'},
                   {"tools": [{"id": "c1", "name": "building_basis", "args": {"use": "공동주택"}}]},
                   {"answer": "주택법 제15조의 사업계획승인부터 받습니다."}])
    await loop.run_query("그럼 내가 어떻게 진행해야해", mcp=Mcp(), model=model, context=ctx)
    fresh = [m for m in model.seen[2] if m.get("role") == "tool" and m.get("tool_call_id") == "c1"][0]
    assert "주택법 제15조" in fresh["content"] and settings.same_result_text.split("{")[0] not in fresh["content"]
