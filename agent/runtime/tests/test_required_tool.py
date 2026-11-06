"""꼭 부를 도구 — tool_choice=required 를 줘도 글로 답하는 서버가 있다(실측 2026-11-05: ollama 는 무시, vLLM gpt-oss 도
인사말엔 글로 답함). 그러면 그 도구의 입력 형식(input_schema)을 구조화 출력(response_format json_schema)으로 걸어 인자만
한 번 더 받아 도구 호출로 바꾼다 — 구조화 출력은 두 서버 모두 형식을 지켰다. 형식 앞에 생각 문장이 새어 나와도 { … } 만 읽는다."""
from types import SimpleNamespace as NS

import loop
from config import settings

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
TOOL = {"name": "law_search", "description": "법령 검색", "input_schema": SCHEMA}
NOTE = "도구 {tool}의 인자만 JSON 으로 채우세요."


def _text(content):
    return NS(choices=[NS(finish_reason="stop", message=NS(content=content, tool_calls=None))],
              usage=NS(prompt_tokens=10, completion_tokens=3))


def _call():
    tc = NS(id="c1", function=NS(name="law_search", arguments='{"query": "산지전용"}'))
    return NS(choices=[NS(finish_reason="tool_calls", message=NS(content=None, tool_calls=[tc]))],
              usage=NS(prompt_tokens=10, completion_tokens=3))


class Client:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.sent.append(kw)
        return self.replies.pop(0)


MSGS = [{"role": "user", "content": "산지에 주택 지으려면 허가 필요해?"}]


async def test_text_reply_to_a_required_tool_is_turned_into_the_call(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_text("산지전용허가가 필요합니다."), _text('Need to provide tool usage.\n{"query": "산지전용허가"}')])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL], require=True)
    assert [(c["name"], c["args"]) for c in step["tools"]] == [("law_search", {"query": "산지전용허가"})]
    second = client.sent[1]
    assert second["response_format"]["json_schema"]["schema"] == SCHEMA and "tools" not in second
    assert second["messages"][-1] == {"role": "user", "content": "도구 law_search의 인자만 JSON 으로 채우세요."}
    assert step["raw_assistant_message"]["tool_calls"][0]["function"]["name"] == "law_search"
    assert step["usage"] == {"tokens_in": 20, "tokens_out": 6}


async def test_a_tool_call_needs_no_second_request(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_call()])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL], require=True)
    assert step["tools"][0]["args"] == {"query": "산지전용"} and len(client.sent) == 1


async def test_unreadable_arguments_keep_the_text_reply(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_text("필요합니다."), _text("모르겠어요")])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL], require=True)
    assert step["answer"] == "필요합니다." and step["usage"] == {"tokens_in": 20, "tokens_out": 6}


async def test_not_required_text_reply_is_an_answer(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_text("필요합니다.")])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL])
    assert step["answer"] == "필요합니다." and len(client.sent) == 1


async def test_without_the_text_there_is_no_second_request(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", "")
    client = Client([_text("필요합니다.")])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL], require=True)
    assert step["answer"] == "필요합니다." and len(client.sent) == 1


OTHER = {"name": "law_article", "description": "조문 원문", "input_schema": {
    "type": "object", "properties": {"law": {"type": "string"}, "article": {"type": "string"}}, "required": ["law"]}}


async def test_with_several_tools_the_reply_names_the_tool_and_its_arguments(monkeypatch):
    # 실측 2026-11-06 qwen3.5:9b(사고 끔): 되돌린 뒤에도 도구 10개를 받고 글로만 답했다 — 도구가 여럿이어도 고르게 한다
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_text("필요합니다."), _text('{"tool": "law_article", "args": {"law": "산지관리법", "article": "제14조"}}')])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL, OTHER], require=True)
    assert [(c["name"], c["args"]) for c in step["tools"]] == [("law_article", {"law": "산지관리법", "article": "제14조"})]
    choices = client.sent[1]["response_format"]["json_schema"]["schema"]["anyOf"]
    assert [(c["properties"]["tool"]["const"], c["properties"]["args"]) for c in choices] == [
        ("law_search", SCHEMA), ("law_article", OTHER["input_schema"])]
    assert client.sent[1]["messages"][-1]["content"] == "도구 law_search, law_article의 인자만 JSON 으로 채우세요."


async def test_with_several_tools_an_unknown_tool_keeps_the_text_reply(monkeypatch):
    monkeypatch.setattr(settings, "require_args_text", NOTE)
    client = Client([_text("필요합니다."), _text('{"tool": "nope", "args": {}}')])
    step = await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL, OTHER], require=True)
    assert step["answer"] == "필요합니다."
