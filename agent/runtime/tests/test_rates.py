"""비율 수치 검증 — 답에 쓴 비율(%, N분의 M, 1/N)이 이번 조회 원문의 비율 값과 맞는지 값으로 대조한다.
'1천분의 2.5'를 0.25%로 맞게 환산하면 통과하고, '1/2.5(0.4%)'처럼 잘못 옮기면 걸린다."""
import loop
import rates
from config import settings


def test_rate_values_are_read_from_legal_notation():
    assert rates.values("과세표준의 1천분의 2.5") == {0.0025}
    assert rates.values("1,000분의 3, 100분의 20") == {0.003, 0.2}
    assert rates.values("0.25% 또는 1/40") == {0.0025, 0.025}


def test_answer_rates_missing_from_sources_are_flagged():
    msgs = [{"role": "tool", "content": "다. 그 밖의 건축물: 과세표준의 1천분의 2.5 가. 골프장: 1천분의 40"}]
    assert rates.unmatched("세율은 0.25%이고 골프장은 4%입니다.", msgs) == []
    assert rates.unmatched("과세표준의 1/2.5(0.4%)를 적용", msgs) == ["1/2.5", "0.4%"]


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"원문": "그 밖의 건축물: 과세표준의 1천분의 2.5"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


async def test_wrong_rate_is_sent_back_once(monkeypatch):
    monkeypatch.setattr(settings, "answer_rate_check", True)
    model = Model([{"tool": "law_article", "args": {}}, {"answer": "세율은 0.4%입니다."}, {"answer": "세율은 0.25%입니다."}])
    raw = await loop.run_query("재산세율?", mcp=Mcp(), model=model)
    assert raw["answer"] == "세율은 0.25%입니다."
    assert "0.4%" in model.seen[-1][-1]["content"]


async def test_rate_check_is_off_unless_configured(monkeypatch):
    monkeypatch.setattr(settings, "answer_rate_check", False)
    model = Model([{"tool": "law_article", "args": {}}, {"answer": "세율은 0.4%입니다."}])
    raw = await loop.run_query("재산세율?", mcp=Mcp(), model=model)
    assert raw["answer"] == "세율은 0.4%입니다."
