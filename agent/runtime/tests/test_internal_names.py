"""답에 흘린 내부 이름 — 이번 실행의 도구 목록에 있는 도구 이름이 답 본문에 나오면 한 번 되돌리고,
그래도 남으면 그 문장을 뺀다. 이름은 MCP 도구 목록에서 읽는다(낱말 목록을 두지 않는다)."""
import loop
from config import settings


def test_tool_names_in_the_answer_are_found():
    names = {"law_article", "law_search"}
    assert loop.internal_names("근거는 law_article 결과의 원문입니다.", names) == ["law_article"]
    assert loop.internal_names("「건축법」 제11조입니다.", names) == []
    assert loop.internal_names("my_law_article_x 는 다른 말", names) == []    # 낱말 경계


def test_sentences_with_tool_names_are_dropped():
    text = "한도는 5만원입니다.\n\n근거는 모두 이번 대화에서 조회된 law_article 결과의 원문입니다. 더 궁금하면 알려 주세요."
    assert loop.drop_internal(text, ["law_article"]) == "한도는 5만원입니다.\n\n더 궁금하면 알려 주세요."


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"원문": "음식물: 5만원"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LEAK = "한도는 5만원입니다. 근거는 law_article 결과입니다."


async def test_tool_name_in_answer_is_sent_back_once(monkeypatch):
    monkeypatch.setattr(settings, "answer_rate_check", False)
    model = Model([{"tool": "law_article", "args": {}}, {"answer": LEAK}, {"answer": "한도는 5만원입니다."}])
    raw = await loop.run_query("음식물 한도?", mcp=Mcp(), model=model)
    assert raw["answer"] == "한도는 5만원입니다."
    assert "law_article" in model.seen[-1][-1]["content"]


async def test_tool_name_left_after_retry_is_dropped(monkeypatch):
    monkeypatch.setattr(settings, "answer_rate_check", False)
    model = Model([{"tool": "law_article", "args": {}}, {"answer": LEAK}, {"answer": LEAK}])
    raw = await loop.run_query("음식물 한도?", mcp=Mcp(), model=model)
    assert raw["answer"] == "한도는 5만원입니다."


async def test_retry_note_asks_not_to_narrate_the_fix(monkeypatch):
    # 되돌린 답을 다시 쓸 때 "조회 결과에 포함된 내용이며" 같은 경위 설명을 덧붙이지 않게 모든 되돌림 문장 끝에 붙인다
    monkeypatch.setattr(settings, "answer_rate_check", False)
    monkeypatch.setattr(settings, "answer_retry_tail", "경위는 적지 마세요.")
    model = Model([{"tool": "law_article", "args": {}}, {"answer": LEAK}, {"answer": "한도는 5만원입니다."}])
    await loop.run_query("음식물 한도?", mcp=Mcp(), model=model)
    assert model.seen[-1][-1]["content"].endswith("경위는 적지 마세요.")
