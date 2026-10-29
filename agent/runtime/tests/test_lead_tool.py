"""먼저 부를 도구 — 질문 정리 단계가 도구 목록(이름과 설명 첫 줄)을 보고 이 질문에 가장 먼저 부를 도구를
RESTATE_TOOL_KEY 칸에 고르면, 첫 라운드는 그 도구 하나만 주고 꼭 부르게 한다(인자는 모델이 채운다).
실측 2026-10-28 공간오케 질의: "최소 분할 면적이 얼마야?"에 분할 도구를 두고도 주소를 되물었고(W4),
"파주시 단독주택 법령"에 건축 근거 도구 대신 검색만 했다(W1). 지시문에 적어도 따르지 않아 런타임이 정한다."""
import loop
from config import settings


class Mcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "split_area", "description": "최소 분할면적 얼마야? — 분할 제한면적을 찾는다.\n자세히", "input_schema": {}},
                {"name": "law_search", "description": "법령을 검색한다.", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        return {"status": "ok", "facts": {"용도지역별": [{"구분": "주거지역: 60제곱미터"}]}}


class Model:
    def __init__(self, script):
        self.script, self.seen, self.tools, self.required = list(script), [], [], []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        self.tools.append([t["name"] for t in tools])
        self.required.append(require)
        return self.script.pop(0)


def _on(monkeypatch, tmp_path, key="도구"):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "restate_tool_key", key)


CALL = {"tools": [{"id": "c1", "name": "split_area", "args": {}}]}


async def test_restatement_sees_the_tool_catalog(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "최소 분할 면적", "짐작": "", "도구": "split_area"}'}, CALL, {"answer": "60제곱미터"}])
    await loop.run_query("최소 분할 면적이 얼마야?", mcp=Mcp(), model=model)
    system = model.seen[0][0]["content"]
    assert "split_area" in system and "분할 제한면적을 찾는다" in system and "자세히" not in system


async def test_picked_tool_is_the_only_one_offered_first_and_must_be_called(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    mcp = Mcp()
    model = Model([{"answer": '{"질문": "최소 분할 면적", "짐작": "", "도구": "split_area"}'}, CALL, {"answer": "60제곱미터"}])
    raw = await loop.run_query("최소 분할 면적이 얼마야?", mcp=mcp, model=model)
    assert model.tools[1] == ["split_area"] and model.required[1] is True
    assert "law_search" in model.tools[2] and model.required[2] is False      # 그 뒤로는 평소대로
    assert mcp.calls == [("split_area", {})] and raw["answer"] == "60제곱미터"


async def test_unknown_or_empty_pick_leaves_the_round_alone(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    for pick in ("없는도구", ""):
        model = Model([{"answer": '{"질문": "q", "짐작": "", "도구": "%s"}' % pick}, {"answer": "답"}])
        await loop.run_query("q", mcp=Mcp(), model=model)
        assert "law_search" in model.tools[1] and model.required[1] is False


async def test_no_catalog_or_pick_without_the_key(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, key="")
    model = Model([{"answer": '{"질문": "q", "짐작": "", "도구": "split_area"}'}, {"answer": "답"}])
    await loop.run_query("q", mcp=Mcp(), model=model)
    assert "split_area" not in model.seen[0][0]["content"] and model.required[1] is False


async def test_only_the_listed_lead_tools_are_offered(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "restate_lead_tools", "split_area")
    model = Model([{"answer": '{"질문": "q", "짐작": "", "도구": "law_search"}'}, {"answer": "답"}])
    await loop.run_query("q", mcp=Mcp(), model=model)
    assert "law_search" not in model.seen[0][0]["content"] and model.required[1] is False
