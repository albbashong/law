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


async def test_restatement_that_invents_a_law_name_is_dropped(monkeypatch, tmp_path):
    # 정리한 질문에 발화에 없는 법령 정식명이 생기면(기억으로 푼 별칭) 붙이지 않는다 — 실측 2026-10-27 PC1
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_law_loose_pattern", r"([가-힣 ]{2,40}?에 관한 법률)")
    reply = '{"질문": "공직자청탁 및 부정청탁 금지에 관한 법률의 음식물 가액 한도", "짐작": ""}'
    model = Model([{"answer": reply}, {"answer": "답"}])
    await loop.run_query("청탁금지법상 음식물 한도는?", mcp=Mcp(), model=model)
    assert model.seen[1][-1]["content"] == "청탁금지법상 음식물 한도는?"


class TermMcp(Mcp):
    def __init__(self):
        self.called = []

    async def list_tools(self):
        return [*await super().list_tools(), {"name": "term_link", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        self.called.append((name, args))
        if args["word"] == "그린벨트":
            return {"status": "ok", "facts": {"말": "그린벨트", "같은_뜻_법령용어": ["개발제한구역"]}, "message": "연계"}
        return {"status": "failed", "facts": {"말": args["word"]}, "message": "연계 없음"}


def _terms_on(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "restate_term_tool", "term_link")
    monkeypatch.setattr(settings, "restate_term_arg", "word")
    monkeypatch.setattr(settings, "restate_term_failed_pattern", ".")


async def test_words_to_check_are_looked_up_in_the_term_links(monkeypatch, tmp_path):
    # 정리 단계가 고른 일상어·별칭은 기억으로 바꾸지 않고 법제처 연계 자료로 확인해 질문 아래에 붙인다
    _terms_on(monkeypatch, tmp_path)
    mcp = TermMcp()
    reply = '{"질문": "그린벨트에서 축대 설치 신고 요건", "짐작": "", "확인할 말": ["그린벨트", "축대", "GB구역"]}'
    model = Model([{"answer": reply}, {"answer": "답"}])
    await loop.run_query("그린벨트에 축대 쌓으려면 신고해야 돼?", mcp=mcp, model=model)
    assert mcp.called == [("term_link", {"word": "그린벨트"}), ("term_link", {"word": "축대"})]   # 발화에 없는 말은 묻지 않는다
    user = model.seen[1][-1]["content"]
    assert user.startswith("그린벨트에 축대 쌓으려면")
    assert "개발제한구역" in user and "연계 없음" in user


async def test_term_lookup_is_off_without_the_tool(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = TermMcp()
    reply = '{"질문": "그린벨트 주택", "짐작": "", "확인할 말": ["그린벨트"]}'
    model = Model([{"answer": reply}, {"answer": "답"}])
    await loop.run_query("그린벨트 주택", mcp=mcp, model=model)
    assert mcp.called == []


async def test_failed_lookups_are_shown_only_for_law_like_words(monkeypatch, tmp_path):
    # 연계가 없는 말: 법령 이름 꼴이면 "확인되지 않음"을 붙이고(지어내지 않게), 다른 말은 붙이지 않는다(풀어 쓴 질문으로 찾게)
    _terms_on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "restate_term_failed_pattern", r"(?:법|령)$")
    mcp = TermMcp()
    reply = '{"질문": "단통법과 제1종일반주거지역", "짐작": "", "확인할 말": ["단통법", "1종 일주"]}'
    model = Model([{"answer": reply}, {"answer": "답"}])
    await loop.run_query("단통법이랑 1종 일주 알려줘", mcp=mcp, model=model)
    user = model.seen[1][-1]["content"]
    assert "단통법: " in user and "1종 일주: " not in user
