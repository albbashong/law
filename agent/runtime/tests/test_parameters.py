"""파라미터 추출 — PARAM_PROMPT 가 있으면 답이 정해진 뒤 한 번, 답에 나온 법령상의 값(건폐율·면적·금액·기간·날짜 등)을
{항목, 값, 단위, 조건, 근거} 목록으로 뽑는다. 값과 근거 법령은 이번 턴 도구 결과에 그대로 있어야 남긴다(지어낸 값은 버린다).
부르는 쪽(공간오케 등)이 답 문장을 다시 읽지 않고 값을 쓰게 하려는 것이다(2026-10-28 요청)."""
import asyncio

import loop
from a2a.store import TaskStore
from a2a.types import TaskState
from config import settings
from executor import Executor


class Mcp:
    async def list_tools(self):
        return [{"name": "split_area", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"용도지역별": [{"구분": "녹지지역: 200제곱미터", "조례": "양평군 건축 조례 제25조제4호"}]}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LOOKUP = {"tool": "split_area", "args": {"address": "다문리 160"}}
ANSWER = {"answer": "녹지지역은 200제곱미터 미만으로 분할할 수 없습니다(양평군 건축 조례 제25조제4호)."}
EXTRACTED = ('[{"항목": "분할 제한면적", "값": "200", "단위": "제곱미터", "조건": "녹지지역", "근거": "양평군 건축 조례 제25조제4호"},'
             ' {"항목": "건폐율", "값": "20", "단위": "퍼센트", "조건": "", "근거": "양평군 건축 조례 제50조"},'
             ' {"항목": "분할 제한면적", "값": "330", "단위": "제곱미터", "조건": "", "근거": "농지법 시행령 제73조"}]')


def _on(monkeypatch, tmp_path):
    (tmp_path / "param.txt").write_text("값을 뽑으세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "param_prompt", "param.txt")


async def test_values_found_in_the_tool_results_are_kept(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([LOOKUP, ANSWER, {"answer": EXTRACTED}])
    raw = await loop.run_query("다문리 160 최소분할면적?", mcp=Mcp(), model=model)
    assert raw["answer"] == ANSWER["answer"]
    assert model.seen[-1][0] == {"role": "system", "content": "값을 뽑으세요."}
    # 20%(원문에 없는 값)·농지법 330㎡(조회하지 않은 법령)는 버린다
    assert raw["parameters"] == [{"항목": "분할 제한면적", "값": "200", "단위": "제곱미터", "조건": "녹지지역",
                                  "근거": "양평군 건축 조례 제25조제4호"}]


async def test_unreadable_extraction_gives_no_parameters(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([LOOKUP, ANSWER, {"answer": "뽑을 수 없음"}])
    raw = await loop.run_query("다문리 160 최소분할면적?", mcp=Mcp(), model=model)
    assert raw["answer"] == ANSWER["answer"] and raw["parameters"] == []


async def test_no_extraction_without_the_prompt():
    model = Model([LOOKUP, ANSWER])
    raw = await loop.run_query("다문리 160 최소분할면적?", mcp=Mcp(), model=model)
    assert len(model.seen) == 2 and not raw.get("parameters")


def _shape(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
            "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}


async def test_executor_publishes_parameters_artifact():
    store = TaskStore()
    params = [{"항목": "분할 제한면적", "값": "200", "단위": "제곱미터", "조건": "녹지지역", "근거": "조례 제25조제4호"}]

    async def rq(query, **kw):
        return {"answer": "답", "tool_results": {}, "stopped": "answer", "messages": [], "usage": {},
                "parameters": params}

    ex = Executor(store, run_query=rq, shape_result=_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "q", resuming=False)
    for _ in range(400):
        if store.get(task.id).status.state is TaskState.COMPLETED:
            break
        await asyncio.sleep(0.005)
    done = store.get(task.id)
    got = next(a for a in done.artifacts if a.name == "parameters").parts[0].data
    assert got == params


async def test_value_written_with_its_own_unit_is_checked_as_written(monkeypatch, tmp_path):
    # "1년 이하"처럼 값에 단위까지 적고 단위 칸에 또 적어도, 값 그대로 원문에 있으면 남긴다
    _on(monkeypatch, tmp_path)
    item = '[{"항목": "분할 제한면적", "값": "200제곱미터", "단위": "제곱미터", "조건": "", "근거": "양평군 건축 조례 제25조제4호"}]'
    raw = await loop.run_query("q", mcp=Mcp(), model=Model([LOOKUP, ANSWER, {"answer": item}]))
    assert len(raw["parameters"]) == 1
