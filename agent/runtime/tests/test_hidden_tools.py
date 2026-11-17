"""HIDDEN_TOOLS — MCP 서버에 있어도 이 에이전트 모델에게는 주지 않는 도구(계획 에이전트만 쓰는 law_route 등).
실측 2026-11-17 qwen3:8b 가 계획 단계를 보고 law_route 를 법령명이 붙은 조문으로 8번 불러 모두 실패."""
import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "조문", "input_schema": {}},
                {"name": "law_route", "description": "경로", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"원문": "x"}}


class Model:
    def __init__(self):
        self.offered = []

    async def next_step(self, messages, tools, require=False):
        self.offered.append([t["name"] for t in tools])
        return {"answer": "답입니다."}


async def test_hidden_tool_is_not_offered(monkeypatch):
    monkeypatch.setattr(settings, "hidden_tools", "law_route")
    model = Model()
    await loop.run_query("건폐율은?", mcp=Mcp(), model=model)
    assert "law_article" in model.offered[0] and "law_route" not in model.offered[0]


async def test_footer_lines_are_separate_paragraphs():
    assert loop.footer_text(["※ 가.", "※ 나."]) == "\n\n※ 가.\n\n※ 나."
