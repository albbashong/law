"""질문 정리 — RESTATE_PROMPT 가 있으면 도구 루프 전에 한 번, 발화를 법률 용어의 중립 질문과 사용자의 짐작으로
나눈다. 발화 원문은 그대로 두고 그 아래 두 줄로 붙인다. 읽을 수 없거나 발화에 없는 숫자를 지어내면 붙이지 않는다."""
import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_search", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {}}


class Model:
    def __init__(self, script):
        self.script, self.seen, self.tools = list(script), [], []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        self.tools.append(tools)
        return self.script.pop(0)


def _on(monkeypatch, tmp_path):
    (tmp_path / "restate.txt").write_text("질문을 정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")


async def test_question_is_restated_in_legal_terms_before_the_tool_loop(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "개발제한구역에서 단독주택 신축 허용 요건", "짐작": "건축이 전혀 안 된다"}'},
                   {"answer": "답"}])
    raw = await loop.run_query("그린벨트는 건물 아예 못 짓는 거 맞지?", mcp=Mcp(), model=model)
    assert raw["answer"] == "답"
    first = model.seen[0]
    assert first[0]["content"] == "질문을 정리하세요." and "그린벨트는 건물 아예" in first[1]["content"]
    assert model.tools[0] == []                                   # 정리는 도구 없이
    user = model.seen[1][-1]["content"]
    assert user.startswith("그린벨트는 건물 아예 못 짓는 거 맞지?")   # 발화 원문은 그대로
    assert "개발제한구역에서 단독주택 신축 허용 요건" in user and "건축이 전혀 안 된다" in user


async def test_no_presumption_line_when_the_question_has_none(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "개발제한구역 주택 신축 근거", "짐작": ""}'}, {"answer": "답"}])
    await loop.run_query("개발제한구역 주택 신축 근거 알려줘", mcp=Mcp(), model=model)
    user = model.seen[1][-1]["content"]
    assert "개발제한구역 주택 신축 근거" in user
    assert settings.restate_presumption_line.split("{")[0] not in user


async def test_unreadable_or_invented_number_restatement_is_dropped(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    for reply in ("정리할 수 없음", '{"질문": "영천시 계획관리지역 건폐율 60퍼센트 여부"}'):
        model = Model([{"answer": reply}, {"answer": "답"}])
        await loop.run_query("영천 계관 건폐율 몇프로?", mcp=Mcp(), model=model)
        assert model.seen[1][-1]["content"] == "영천 계관 건폐율 몇프로?"


async def test_no_restatement_without_prompt_or_when_resuming(monkeypatch, tmp_path):
    model = Model([{"answer": "답"}])
    await loop.run_query("q", mcp=Mcp(), model=model)
    assert len(model.seen) == 1                                   # 프롬프트 없음
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": "답"}])
    prior = {"messages": [{"role": "user", "content": "앞 질문"}, {"role": "assistant", "content": "어느 시군구인가요?"}]}
    await loop.run_query("영천시", mcp=Mcp(), model=model, prior=prior)
    assert len(model.seen) == 1                                   # 되묻기에 답하는 재개
