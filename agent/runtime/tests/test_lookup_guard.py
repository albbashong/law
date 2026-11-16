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


async def test_answer_citing_articles_without_any_lookup_is_sent_back_even_with_no_tool_picked(monkeypatch, tmp_path):
    # 정리 단계가 도구를 비워도, 조회 없이 조문을 인용한 답은 근거가 없다 — 실측 2026-11-06 qwen3.5:9b "건축신고만으로
    # 집 지을 수 있는 경우가 있어?": 도구를 고르지 않아 조회 0회에 제11조·제23조를 인용한 답이 나갔다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    model = Model([NOT_PICKED, {"answer": "제14조에 따라 됩니다."}, LOOKUP, {"answer": "제25조에 따라 됩니다."}])
    raw = await loop.run_query("건축신고만으로 집 지을 수 있어?", mcp=Mcp(), model=model)
    assert model.seen[2][-1] == {"role": "user", "content": NOTE} and model.required[2] is True
    assert raw["answer"] == "제25조에 따라 됩니다." and len(raw["calls"]) == 1


async def test_answer_without_citations_and_no_tool_picked_is_kept(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    model = Model([NOT_PICKED, {"answer": "안녕하세요."}])
    raw = await loop.run_query("안녕?", mcp=Mcp(), model=model)
    assert raw["answer"] == "안녕하세요." and len(model.seen) == 2


async def test_round_sent_back_for_an_unlooked_up_citation_must_call_a_tool(monkeypatch, tmp_path):
    # 실측 2026-11-09 120b '서대문구에서 단독주택 짓으려면': 건축법 제11조를 조회 없이 인용해 되돌렸더니 조회하지 않고
    # 다시 써서 확인 안 된 인용으로 나갔다 — 되돌린 라운드는 도구를 꼭 부르게 한다(조회 없는 답 되돌림과 같이)
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    model = Model([PICKED, LOOKUP, {"answer": "제11조에 따라 허가를 받습니다."},
                   LOOKUP, {"answer": "제25조에 따라 허가를 받습니다."}])
    raw = await loop.run_query("산지에 집 지으려면 어떻게 해?", mcp=Mcp(), model=model)
    assert "제11조" in model.seen[3][-1]["content"]
    assert model.required[3] is True and model.required[4] is False
    assert raw["answer"].startswith("제25조에 따라")


class PointerMcp(Mcp):
    async def call(self, name, args):
        return {"pointer": ["핵심_조문"], "status": "ok", "facts": {"핵심_조문": ["건축법 제11조(건축허가)"]}}


async def test_a_cite_only_in_a_list_the_tool_marked_as_pointers_is_sent_back(monkeypatch, tmp_path):
    # 도구가 결과 최상위 POINTER_KEY 로 '열어 볼 조문 목록'이라고 표시한 칸에만 나온 조문은 원문을 열어야 인용할 수 있다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    model = Model([PICKED, LOOKUP, {"answer": "제11조에 따라 허가를 받습니다."},
                   LOOKUP, {"answer": "허가를 받습니다."}])
    raw = await loop.run_query("산지에 집 지으려면 어떻게 해?", mcp=PointerMcp(), model=model)
    assert "제11조" in model.seen[3][-1]["content"]
    assert "pointer" not in model.seen[2][-1]["content"]
    assert raw["answer"].startswith("허가를 받습니다")


async def test_a_cite_only_in_a_pointer_list_is_sent_back_asking_to_open_it(monkeypatch, tmp_path):
    # 실측 2026-11-10 W1: 열어 볼 목록에만 있던 건축법 제14·21·22조를 되돌렸더니 그 조문을 열지 않고 같은 building_basis 를
    # 다시 불렀다 — 목록에만 있는 인용은 '그 조문 원문을 열라'는 문장으로 따로 되돌린다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    monkeypatch.setattr(settings, "answer_pointer_retry_text", "목록에만 있고 원문을 안 열었음: {cites}")
    model = Model([PICKED, LOOKUP, {"answer": "제11조와 제30조에 따라 허가를 받습니다."},
                   LOOKUP, {"answer": "허가를 받습니다."}])
    await loop.run_query("산지에 집 지으려면 어떻게 해?", mcp=PointerMcp(), model=model)
    note = model.seen[3][-1]["content"]
    assert "목록에만 있고 원문을 안 열었음: 제11조" in note
    assert "제30조" in note and "목록에만 있고 원문을 안 열었음: 제11조, 제30조" not in note
