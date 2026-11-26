"""빈 답·잘린 답은 최종 답이 아니다 (전수조사 S7) — 토큰 한도에 걸리거나 서버가 빈 글을 주면 예전에는 맺음 문장만 붙여
'완료'로 나갔다. 한 번 다시 받고, 그래도 비면 실패로 끝낸다."""
from types import SimpleNamespace as NS

import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_search", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


async def test_empty_answer_is_asked_again_once():
    model = Model([{"answer": "  "}, {"answer": "안녕하세요."}])
    raw = await loop.run_query("안녕", mcp=Mcp(), model=model)
    assert raw["stopped"] == "answer" and raw["answer"].startswith("안녕하세요.")
    assert model.seen[1][-1] == {"role": "user", "content": settings.empty_answer_text}


async def test_answer_cut_at_the_token_limit_is_asked_again():
    model = Model([{"answer": "제1조에 따르면", "cut": True}, {"answer": "안녕하세요."}])
    raw = await loop.run_query("안녕", mcp=Mcp(), model=model)
    assert raw["answer"].startswith("안녕하세요.")


async def test_still_empty_ends_as_a_failure_not_a_bare_closing_line():
    raw = await loop.run_query("안녕", mcp=Mcp(), model=Model([{"answer": ""}, {"answer": ""}]))
    assert raw["stopped"] == "empty" and raw["answer"] == ""


class Client:
    def __init__(self, reply):
        self.chat = NS(completions=NS(create=self.create))
        self.reply = reply

    async def create(self, **kw):
        return self.reply


def _reply(finish, content=None, tool_calls=None):
    return NS(choices=[NS(finish_reason=finish, message=NS(content=content, tool_calls=tool_calls))],
              usage=NS(prompt_tokens=1, completion_tokens=1))


async def test_tool_calls_are_kept_whatever_the_finish_reason():
    tc = NS(id="c1", function=NS(name="law_search", arguments='{"query": "산지전용"}'))
    step = await loop.ModelAdapter(client=Client(_reply("stop", tool_calls=[tc]))).next_step([], [])
    assert [(c["name"], c["args"]) for c in step["tools"]] == [("law_search", {"query": "산지전용"})]


async def test_adapter_marks_an_answer_cut_by_length():
    step = await loop.ModelAdapter(client=Client(_reply("length", content="제1조에"))).next_step([], [])
    assert step["answer"] == "제1조에" and step["cut"] is True
    step = await loop.ModelAdapter(client=Client(_reply("stop", content="끝."))).next_step([], [])
    assert not step.get("cut")


# ── 읽지 못한 도구 인자는 빈 인자로 부르지 않는다 ───────────────────────────────────────────────
async def test_adapter_marks_unreadable_tool_arguments():
    tc = NS(id="c1", function=NS(name="law_search", arguments='{"query": "산지전'))
    step = await loop.ModelAdapter(client=Client(_reply("tool_calls", tool_calls=[tc]))).next_step([], [])
    assert step["tools"][0]["args"] is None


class CountingMcp(Mcp):
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append(args)
        return {"status": "ok", "facts": {}}


async def test_call_with_unreadable_arguments_is_sent_back_not_run():
    mcp = CountingMcp()
    model = Model([{"tools": [{"id": "c1", "name": "law_search", "args": None}]}, {"answer": "안녕하세요."}])
    await loop.run_query("안녕", mcp=mcp, model=model)
    assert mcp.calls == []
    assert model.seen[1][-1]["content"] == settings.args_unreadable_text.format(name="law_search")
