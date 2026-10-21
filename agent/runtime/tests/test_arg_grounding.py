"""도구 인자 검증 — ARG_GROUNDED_KEYS 의 값(주소·지역)이 사용자 발화·도구 결과에 없으면 실행하지 않는다.

실측 2026-10-20: "예시를 줘봐" 뒤 모델이 "서울특별시 강남구 대치동 123-45 / 공장"을 지어내 조회했다.
"""

import loop
from config import settings


class RecMcp:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"status": "ok", "facts": {}}

    async def list_tools(self):
        return [{"name": "basis", "description": "b", "input_schema": {}},
                {"name": "find", "description": "f", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, args))
        return self.result


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    async def next_step(self, messages, tools):
        self.seen.append(list(messages))
        return self.script.pop(0)


def on(monkeypatch):
    monkeypatch.setattr(settings, "arg_grounded_keys", "address,region")


async def test_invented_address_is_not_executed(monkeypatch):
    on(monkeypatch)
    mcp = RecMcp()
    model = FakeModel([{"tool": "basis", "args": {"use": "공장", "address": "서울특별시 강남구 대치동 123-45"}},
                       {"answer": "주소를 알려 주세요."}])
    raw = await loop.run_query("수의계약은 금액이 얼마까지야?", mcp=mcp, model=model)
    assert mcp.calls == []
    fed = model.seen[1][-1]["content"]
    assert "address" in fed and "대치동 123-45" in fed
    assert raw["stopped"] == "answer"


async def test_address_said_by_user_runs_even_when_province_is_added(monkeypatch):
    # 시도를 붙여 쓰는 건 정상 — 번지와 지명 하나가 발화에 있으면 통과
    on(monkeypatch)
    mcp = RecMcp()
    model = FakeModel([{"tool": "basis", "args": {"address": "경상북도 경산시 중방동 701-17"}}, {"answer": "답"}])
    await loop.run_query("경산시 중방동 701-17 에 단독주택 지을 수 있어?", mcp=mcp, model=model)
    assert len(mcp.calls) == 1


async def test_different_lot_number_is_not_executed(monkeypatch):
    on(monkeypatch)
    mcp = RecMcp()
    model = FakeModel([{"tool": "basis", "args": {"address": "경산시 중방동 702"}}, {"answer": "답"}])
    await loop.run_query("경산시 중방동 701-17 에 단독주택", mcp=mcp, model=model)
    assert mcp.calls == []


async def test_value_from_tool_result_runs(monkeypatch):
    on(monkeypatch)
    mcp = RecMcp({"status": "ok", "facts": {"시군구": "영천시"}})
    model = FakeModel([{"tool": "find", "args": {"q": "덕연리 453"}},
                       {"tool": "basis", "args": {"region": "영천시"}}, {"answer": "답"}])
    await loop.run_query("덕연리 453 단독주택", mcp=mcp, model=model)
    assert [c[0] for c in mcp.calls] == ["find", "basis"]


async def test_disabled_when_keys_empty(monkeypatch):
    monkeypatch.setattr(settings, "arg_grounded_keys", "")
    mcp = RecMcp()
    model = FakeModel([{"tool": "basis", "args": {"address": "서울특별시 강남구 대치동 123-45"}}, {"answer": "답"}])
    await loop.run_query("x", mcp=mcp, model=model)
    assert len(mcp.calls) == 1
