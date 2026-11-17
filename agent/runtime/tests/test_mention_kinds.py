"""꼭 나올 이름(mention)의 종류 — 도구가 {"name","kind","label"} 로 표시하면 되돌림에는 label 을, 되돌린 뒤에도 빠지면
종류별 문장(MENTION_APPEND_TEXTS)으로 붙인다. 종류의 문장이 없으면 붙이지 않는다(판단 근거 조문 등). 글자만 준 이름은
예전처럼 MENTION_APPEND_TEXT(필지 규제)로."""
import loop
from config import settings

REVIEW = {"name": "제34조", "kind": "추가검토", "label": "농지의 전용허가(농지법 제34조)"}
ARTICLE = {"name": "별표 17", "kind": "조문", "label": "별표 17"}


class Mcp:
    def __init__(self, marks):
        self.marks = marks

    async def list_tools(self):
        return [{"name": "building_basis", "description": "건축", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "mention": self.marks, "facts": {"용도지역": "자연녹지지역"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


CALL = {"tools": [{"id": "c1", "name": "building_basis", "args": {"use": "주택"}}]}


def _texts(monkeypatch):
    monkeypatch.setattr(settings, "mention_append_text", "\n\n※ 규제: {names}")
    monkeypatch.setattr(settings, "mention_append_texts", {"추가검토": "\n\n※ 함께 확인할 것: {names}"})
    monkeypatch.setattr(settings, "mention_retry_text", "빠짐: {names}")


async def test_retry_names_the_label(monkeypatch):
    _texts(monkeypatch)
    model = Model([CALL, {"answer": "자연녹지지역입니다."}, {"answer": "농지법 제34조도 보세요."}])
    await loop.run_query("주택 돼?", mcp=Mcp([REVIEW]), model=model)
    assert "빠짐: 농지의 전용허가(농지법 제34조)" in model.seen[2][-1]["content"]


async def test_still_missing_is_appended_with_the_text_of_its_kind(monkeypatch):
    _texts(monkeypatch)
    raw = await loop.run_query("주택 돼?", mcp=Mcp([REVIEW, ARTICLE, "개발제한구역"]),
                               model=Model([CALL, {"answer": "자연녹지지역입니다."}, {"answer": "자연녹지지역입니다."}]))
    assert "※ 함께 확인할 것: 농지의 전용허가(농지법 제34조)" in raw["answer"]
    assert "※ 규제: 개발제한구역" in raw["answer"]
    assert "별표 17" not in raw["answer"]          # 조문 종류는 붙일 문장이 없다 — 되돌림만


async def test_name_found_in_the_answer_is_not_sent_back(monkeypatch):
    _texts(monkeypatch)
    model = Model([CALL, {"answer": "농지법 제34조의 전용허가와 별표 17을 확인하세요."}])
    raw = await loop.run_query("주택 돼?", mcp=Mcp([REVIEW, ARTICLE]), model=model)
    assert len(model.seen) == 2 and "함께 확인할 것" not in raw["answer"]
