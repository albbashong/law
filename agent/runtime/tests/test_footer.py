"""도구가 '답 끝에 꼭 붙일 줄'(footer)로 표시한 문장 — 답에 없으면 끝에 붙인다. 모델에게는 보이지 않는다."""
import loop
from config import settings

LINE = "※ 필지의 용도지역·지목은 토지이용계획 기준입니다."


class Mcp:
    async def list_tools(self):
        return [{"name": "parcel_zoning", "description": "필지", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"용도지역": ["자연녹지지역"]}, settings.footer_key: [LINE]}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


CALL = {"tools": [{"id": "c1", "name": "parcel_zoning", "args": {"address": "영천시 신월리 1-51"}}]}


async def test_footer_is_appended_when_missing():
    raw = await loop.run_query("이 땅 용도지역?", mcp=Mcp(), model=Model([CALL, {"answer": "자연녹지지역입니다."}]))
    assert raw["answer"].rstrip().endswith(LINE)


async def test_footer_already_in_the_answer_is_not_repeated():
    raw = await loop.run_query("이 땅 용도지역?", mcp=Mcp(), model=Model([CALL, {"answer": f"자연녹지지역입니다.\n{LINE}"}]))
    assert raw["answer"].count("토지이용계획 기준") == 1


async def test_footer_is_not_shown_to_the_model():
    model = Model([CALL, {"answer": "자연녹지지역입니다."}])
    await loop.run_query("이 땅 용도지역?", mcp=Mcp(), model=model)
    tool_msgs = [m for m in model.seen[1] if m["role"] == "tool"]
    assert tool_msgs and all("토지이용계획 기준" not in m["content"] for m in tool_msgs)
