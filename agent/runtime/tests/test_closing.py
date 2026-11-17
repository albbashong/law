"""마무리 문장(ANSWER_CLOSING_TEXT) — 런타임이 답의 맨 끝(※ 알림·이야기 뒤)에 붙인다. 모델이 본문 끝에 비슷한 문장을
이미 썼으면 그 문단은 뺀다(실측 2026-11-17 qwen3:8b: 본문 끝 마무리 → ※ 알림 → 이야기 순으로 마무리가 중간에 끼었다)."""
import loop
from config import settings

CLOSE = "더 필요한 내용이 있으면 알려 주세요."
FOOT = "※ 필지의 용도지역·지목은 토지이용계획 기준입니다."


class Mcp:
    async def list_tools(self):
        return [{"name": "parcel_zoning", "description": "필지", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"용도지역": ["자연녹지지역"]}, settings.footer_key: [FOOT]}


class Model:
    def __init__(self, script):
        self.script = list(script)

    async def next_step(self, messages, tools, require=False):
        return self.script.pop(0)


CALL = {"tools": [{"id": "c1", "name": "parcel_zoning", "args": {"address": "영천시 신월리 1-51"}}]}


def test_model_closing_paragraph_is_dropped(monkeypatch):
    monkeypatch.setattr(settings, "answer_closing_text", CLOSE)
    assert loop.drop_closing("본문입니다.\n\n더 필요하신 정보가 있으시면 알려주세요.") == "본문입니다."
    assert loop.drop_closing("본문입니다.\n\n농지전용허가도 확인하세요.") == "본문입니다.\n\n농지전용허가도 확인하세요."


async def test_closing_is_the_last_line_after_runtime_notes(monkeypatch):
    monkeypatch.setattr(settings, "answer_closing_text", CLOSE)
    raw = await loop.run_query("이 땅 용도지역?", mcp=Mcp(), model=Model(
        [CALL, {"answer": "자연녹지지역입니다.\n\n더 필요하신 정보가 있으시면 알려주세요."}]))
    assert raw["answer"].endswith(f"{FOOT}\n\n{CLOSE}")
    assert raw["answer"].count("알려") == 1


async def test_no_closing_text_leaves_the_answer_alone(monkeypatch):
    monkeypatch.setattr(settings, "answer_closing_text", "")
    raw = await loop.run_query("이 땅 용도지역?", mcp=Mcp(), model=Model([CALL, {"answer": "자연녹지지역입니다."}]))
    assert raw["answer"].endswith(FOOT)
