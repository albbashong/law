"""답 다듬기 — (1) 런타임이 메시지에 넣은 지시 문장(정리 줄·계획 안내·되돌림 문장)이 답에 새면 지운다(실측 2026-11-16
qwen3:8b 가 첫 줄에 '… — 법령 검색어와 이 에이전트가 답할 질문인지의 판단은 이 질문으로 하세요.'를 옮겨 씀).
(2) 도구가 답에 꼭 나와야 한다고 표시한 이름(필지 규제)이 되돌린 뒤에도 빠지면 MENTION_APPEND_TEXT 로 답 끝에 붙인다."""
import loop
from config import settings

LINE = "[법률 용어로 정리한 질문] {question} — 법령 검색어와 이 에이전트가 답할 질문인지의 판단은 이 질문으로 하세요."


def test_injected_instruction_is_removed_from_the_answer(monkeypatch):
    monkeypatch.setattr(settings, "restate_question_line", LINE)
    answer = ("1. **요약**: 영천시 신월리 1-51에 공장을 지을 수 있는지 확인해 주세요 — 법령 검색어와 이 에이전트가 답할 질문인지의 "
              "판단은 이 질문으로 하세요.\n2. 근거: 제71조")
    out = loop.strip_injected(answer)
    assert "법령 검색어와" not in out and "영천시 신월리 1-51에 공장을" in out and "제71조" in out


def test_answer_without_injected_text_is_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "restate_question_line", LINE)
    assert loop.strip_injected("건폐율은 제77조에 따릅니다.") == "건폐율은 제77조에 따릅니다."


class Mcp:
    async def list_tools(self):
        return [{"name": "parcel_zoning", "description": "필지", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "mention": ["가축사육제한구역"], "facts": {"용도지역": "자연녹지지역"}}


class Model:
    def __init__(self, script):
        self.script = list(script)

    async def next_step(self, messages, tools, require=False):
        return self.script.pop(0)


CALL = {"tools": [{"id": "c1", "name": "parcel_zoning", "args": {"address": "영천시 1-51"}}]}


async def test_mentions_still_missing_are_appended(monkeypatch):
    monkeypatch.setattr(settings, "mention_append_text", "\n\n※ 이 땅에 걸린 규제도 확인하세요: {names}")
    raw = await loop.run_query("공장 돼?", mcp=Mcp(), model=Model([CALL, {"answer": "자연녹지지역입니다."},
                                                                 {"answer": "자연녹지지역입니다."}]))
    assert raw["answer"].endswith("※ 이 땅에 걸린 규제도 확인하세요: 가축사육제한구역")


async def test_mentions_are_not_appended_when_the_text_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "mention_append_text", "")
    raw = await loop.run_query("공장 돼?", mcp=Mcp(), model=Model([CALL, {"answer": "자연녹지지역입니다."},
                                                                 {"answer": "자연녹지지역입니다."}]))
    assert raw["answer"] == "자연녹지지역입니다."
