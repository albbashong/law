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

    async def next_step(self, messages, tools):
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
