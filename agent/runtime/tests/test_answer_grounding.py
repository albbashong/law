"""답변 인용 검증 — ANSWER_CITE_PATTERN 이 잡은 인용이 이번 대화의 도구 결과에 없으면 한 번 되돌린다."""

import loop
from config import settings

CITE = r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?"


class FakeMcp:
    async def list_tools(self):
        return [{"name": "law", "description": "law", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"article": "제84조제1항"}, {"article": "별표 17"}]}}


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def next_step(self, messages, tools):
        self.seen.append(list(messages))
        return self.script.pop(0)


async def test_ungrounded_citation_is_sent_back_once(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    model = FakeModel([{"tool": "law", "args": {}},
                       {"answer": "제84조와 제14조, 별표 1을 보세요."},
                       {"answer": "제84조와 별표 17을 보세요."}])
    raw = await loop.run_query("건폐율", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제84조와 별표 17을 보세요."
    back = model.seen[2][-1]["content"]
    assert "제14조" in back and "별표 1" in back and "제84조" not in back
    assert raw["ungrounded"] == []


async def test_grounded_answer_passes_without_extra_round(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    model = FakeModel([{"tool": "law", "args": {}}, {"answer": "제84조 제1항과 별표 17입니다."}])
    raw = await loop.run_query("건폐율", mcp=FakeMcp(), model=model)
    assert raw["stopped"] == "answer" and len(model.seen) == 2


async def test_still_ungrounded_after_retry_is_reported_not_looped(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    model = FakeModel([{"tool": "law", "args": {}}, {"answer": "제14조"}, {"answer": "제14조입니다"}])
    raw = await loop.run_query("건폐율", mcp=FakeMcp(), model=model)
    assert raw["stopped"] == "answer" and raw["ungrounded"] == ["제14조"]


async def test_disabled_when_pattern_empty(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", "")
    model = FakeModel([{"answer": "제14조"}])
    raw = await loop.run_query("x", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제14조" and len(model.seen) == 1


PAIR = r"(?P<scope>[^|\n]*?)\s*\|?\s*(?P<cite>" + CITE + r")"


class PairMcp(FakeMcp):
    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [
            {"law": "국토의 계획 및 이용에 관한 법률 시행령", "article": "제84조제1항제16호"},
            {"law": "건축법 시행령", "article": "별표 1 제19호"}]}}


async def test_citation_paired_with_wrong_law_is_sent_back(monkeypatch):
    # 조문 번호는 도구 결과에 있지만 다른 법령의 것 — 번호만 보는 검증은 못 잡는다
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    monkeypatch.setattr(settings, "answer_pair_pattern", PAIR)
    monkeypatch.setattr(settings, "answer_pair_keys", "law,article")
    model = FakeModel([{"tool": "law", "args": {}},
                       {"answer": "| 건축법 시행령 | 제84조제1항제16호 | 건폐율 |\n| 건축법 시행령 | 별표 1 제19호 | 주유소 |"},
                       {"answer": "| 국토의 계획 및 이용에 관한 법률 시행령 | 제84조 | 건폐율 |"}])
    raw = await loop.run_query("건폐율", mcp=PairMcp(), model=model)
    back = model.seen[2][-1]["content"]
    assert "건축법 시행령 제84조" in back and "별표 1" not in back
    assert raw["ungrounded"] == []


async def test_unknown_scope_is_not_judged(monkeypatch):
    # 앞말이 도구 결과의 어느 법령명으로도 끝나지 않으면(약칭·문장) 짝을 판단하지 않는다
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    monkeypatch.setattr(settings, "answer_pair_pattern", PAIR)
    monkeypatch.setattr(settings, "answer_pair_keys", "law,article")
    model = FakeModel([{"tool": "law", "args": {}}, {"answer": "시행령에 따르면 제84조가 기준입니다."}])
    raw = await loop.run_query("건폐율", mcp=PairMcp(), model=model)
    assert raw["stopped"] == "answer" and len(model.seen) == 2


async def test_still_ungrounded_is_marked_in_answer_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    monkeypatch.setattr(settings, "answer_unverified_text", "\n\n※ 확인되지 않은 인용: {cites}")
    model = FakeModel([{"tool": "law", "args": {}}, {"answer": "제14조"}, {"answer": "제14조입니다"}])
    raw = await loop.run_query("건폐율", mcp=FakeMcp(), model=model)
    assert raw["answer"] == "제14조입니다\n\n※ 확인되지 않은 인용: 제14조"
