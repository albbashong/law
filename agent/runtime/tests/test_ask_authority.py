"""되묻기는 루프가 정한다 (전수조사 S5) — 후보를 고르라는 결과 뒤에 다른 조회로 확인해 답을 냈으면, 성형이 그 묵은 결과를
보고 확인된 답을 되묻기로 뒤집지 않는다."""
from a2a.store import TaskStore
from a2a.types import TaskState

from config import settings
from executor import Executor

STALE = {"status": "needs_disambiguation", "message": "어느 필지인가요?",
         "facts": {"candidates": [{"pnu": "1", "label": "신월리 1-51"}, {"pnu": "2", "label": "신월리 1-52"}]}}


def answered(**more):
    async def run(query, *, on_progress=None, prior=None, **kw):
        return {"answer": "자연녹지지역입니다.", "stopped": "answer", "usage": {}, "errors": [], "messages": [],
                "tool_results": {"parcel_zoning": STALE, "law_article": {"status": "ok", "facts": {}}}, **more}
    return run


async def _run(monkeypatch, run_query):
    monkeypatch.setattr(settings, "clarify_tools", "parcel_zoning")
    store = TaskStore()
    task = store.create()
    store.transition(task.id, TaskState.WORKING)
    await Executor(store, run_query=run_query).run(task.id, "이 땅 용도지역?", resuming=False)
    return store.get(task.id).status.state


async def test_verified_answer_is_not_flipped_into_a_question(monkeypatch):
    # 고르라는 결과(parcel_zoning) 뒤에 다른 조회(law_article)로 확인해 답했다 — 루프가 '고를 것이 남지 않았다'고 알린다
    assert await _run(monkeypatch, answered(pending_choice=None)) == TaskState.COMPLETED


async def test_choice_still_pending_is_asked(monkeypatch):
    # 마지막 조회가 고르라고 했는데 후보에 이름표가 없어 루프가 직접 되묻지 못했다 — 성형이 되묻는다
    assert await _run(monkeypatch, answered(pending_choice="parcel_zoning")) == TaskState.INPUT_REQUIRED


async def test_loop_that_does_not_report_leaves_it_to_the_shape(monkeypatch):
    assert await _run(monkeypatch, answered()) == TaskState.INPUT_REQUIRED


class Mcp:
    def __init__(self):
        self.n = 0

    async def list_tools(self):
        return [{"name": n, "description": "", "input_schema": {}} for n in ("parcel_zoning", "law_article")]

    async def call(self, name, args):
        return STALE if name == "parcel_zoning" else {"status": "ok", "facts": {"원문": "자연녹지지역"}}


class Model:
    def __init__(self, script):
        self.script = list(script)

    async def next_step(self, messages, tools, require=False):
        return self.script.pop(0)


async def test_loop_reports_whether_a_choice_is_still_pending(monkeypatch):
    import loop
    monkeypatch.setattr(settings, "disambiguation_status", "needs_disambiguation")
    first = {"tools": [{"id": "c1", "name": "parcel_zoning", "args": {"address": "신월리 1"}}]}
    then = {"tools": [{"id": "c2", "name": "law_article", "args": {"law": "x", "article": "제1조"}}]}
    raw = await loop.run_query("q", mcp=Mcp(), model=Model([first, then, {"answer": "자연녹지지역입니다."}]))
    assert raw["stopped"] == "answer" and raw["pending_choice"] is None
