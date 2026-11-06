"""조회 없이 낸 답 — 질문 정리 단계가 먼저 부를 도구를 골랐는데(근거를 찾아야 하는 질문) 한 번도 조회하지 않고
답하면, ANSWER_NO_LOOKUP_TEXT 를 붙여 한 번 되돌린다. 실측 2026-11-05 로컬 모델(qwen3.5:9b): 서버가
tool_choice=required 를 지키지 않아 첫 라운드에 글로 답했고, 인용이 없는 답은 대조할 것이 없어 그대로 나갔다(산지 질문:
조회 0회에 없는 법령명). 그리고 확인 결과(조회 수·확인 안 된 인용)를 verification 아티팩트로 싣는다 — 보류할지는
부르는 쪽이 정한다."""
import asyncio

import loop
from a2a.store import TaskStore
from a2a.types import TaskState
from config import settings
from executor import Executor


class Mcp:
    async def list_tools(self):
        return [{"name": "law_search", "description": "법령을 검색한다.", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}]}}


class Model:
    def __init__(self, script):
        self.script, self.seen, self.required = list(script), [], []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        self.required.append(require)
        return self.script.pop(0)


NOTE = "먼저 근거를 조회하세요."
PICKED = {"answer": '{"질문": "산지전용허가", "짐작": "", "도구": "law_search"}'}
NOT_PICKED = {"answer": '{"질문": "인사", "짐작": "", "도구": ""}'}
LOOKUP = {"tools": [{"id": "c1", "name": "law_search", "args": {"query": "산지전용"}}]}


def _on(monkeypatch, tmp_path, note=NOTE):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "restate_tool_key", "도구")
    monkeypatch.setattr(settings, "answer_no_lookup_text", note)


async def test_answer_without_any_lookup_is_sent_back_once(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([PICKED, {"answer": "산지전용허가가 필요합니다."}, LOOKUP, {"answer": "제25조에 따라 필요합니다."}])
    raw = await loop.run_query("산지에 집 지으려면 허가 필요해?", mcp=Mcp(), model=model)
    assert model.seen[2][-1] == {"role": "user", "content": NOTE}
    assert raw["answer"] == "제25조에 따라 필요합니다." and len(raw["calls"]) == 1
    # 되돌린 라운드는 도구를 꼭 부르게 한다 — 실측 2026-11-06 qwen3.5:9b: 문장만 붙여 되돌리면 또 글로 답했다
    assert model.required[2] is True and model.required[3] is False


async def test_sent_back_only_once(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([PICKED, {"answer": "필요합니다."}, {"answer": "그래도 필요합니다."}])
    raw = await loop.run_query("산지에 집 지으려면 허가 필요해?", mcp=Mcp(), model=model)
    assert raw["answer"] == "그래도 필요합니다." and raw["calls"] == []


async def test_question_needing_no_tool_is_answered_directly(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([NOT_PICKED, {"answer": "안녕하세요."}])
    raw = await loop.run_query("안녕?", mcp=Mcp(), model=model)
    assert raw["answer"] == "안녕하세요." and len(model.seen) == 2


async def test_without_the_text_the_answer_is_kept(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, note="")
    model = Model([PICKED, {"answer": "필요합니다."}])
    raw = await loop.run_query("산지에 집 지으려면 허가 필요해?", mcp=Mcp(), model=model)
    assert raw["answer"] == "필요합니다."


def _shape(answer, tool_results, usage=None):
    return {"message": answer, "status": "ok", "candidates": [], "resolve": None,
            "actions": [], "tool_results": dict(tool_results), "usage": dict(usage or {})}


async def _verification(raw):
    store = TaskStore()

    async def rq(query, **kw):
        return {"tool_results": {}, "stopped": "answer", "messages": [], "usage": {}, **raw}

    ex = Executor(store, run_query=rq, shape_result=_shape)
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await ex.run(task.id, "q", resuming=False)
    for _ in range(400):
        if store.get(task.id).status.state is TaskState.COMPLETED:
            break
        await asyncio.sleep(0.005)
    return next(a for a in store.get(task.id).artifacts if a.name == "verification").parts[0].data


async def test_executor_reports_lookups_and_unverified_cites():
    got = await _verification({"answer": "답", "calls": [], "ungrounded": ["제14조", "산지보전법"]})
    assert got == {"lookups": 0, "unverified": ["제14조", "산지보전법"]}


async def test_executor_reports_a_checked_answer_too():
    got = await _verification({"answer": "답", "calls": [{"tool": "law_search", "args": {}}], "ungrounded": []})
    assert got == {"lookups": 1, "unverified": []}
