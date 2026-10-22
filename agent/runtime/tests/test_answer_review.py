"""답 점검 — 인용·수치 검증을 통과한 답을 ANSWER_REVIEW_PROMPT 로 조회 원문과 한 번 대조해, 고칠 점이 나오면
그것을 되돌려 다시 답하게 한다(한 번만). 점검 답을 읽을 수 없거나 문제가 없으면 답을 그대로 낸다."""
import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"원문": "출입에 지장이 없다고 인정되는 경우"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LOOKUP = {"tool": "law_article", "args": {"law": "건축법", "article": "제44조"}}


def _on(monkeypatch, tmp_path):
    (tmp_path / "review.txt").write_text("답을 점검하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "answer_review_prompt", "review.txt")


async def test_problems_found_by_review_are_sent_back_once(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([LOOKUP, {"answer": "예외에 해당하므로 건축 가능합니다."},
                   {"answer": '{"문제": ["허가권자가 인정해야 하는 예외를 해당한다고 단정"]}'},
                   {"answer": "예외는 허가권자가 인정할 때만 적용됩니다."}])
    raw = await loop.run_query("내부 통로가 있으면 돼?", mcp=Mcp(), model=model)
    assert raw["answer"] == "예외는 허가권자가 인정할 때만 적용됩니다."
    review_call = model.seen[2]
    assert review_call[0]["content"] == "답을 점검하세요." and "출입에 지장이" in review_call[1]["content"]
    assert "허가권자가 인정해야 하는 예외" in model.seen[3][-1]["content"]


async def test_clean_or_unreadable_review_keeps_the_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    for verdict in ('{"문제": []}', "읽을 수 없는 점검"):
        model = Model([LOOKUP, {"answer": "답"}, {"answer": verdict}])
        raw = await loop.run_query("q", mcp=Mcp(), model=model)
        assert raw["answer"] == "답" and len(model.seen) == 3


async def test_no_review_without_lookups_or_prompt(monkeypatch, tmp_path):
    model = Model([LOOKUP, {"answer": "답"}])
    assert (await loop.run_query("q", mcp=Mcp(), model=model))["answer"] == "답"   # 프롬프트 없음
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": "되묻는 답"}])
    assert (await loop.run_query("q", mcp=Mcp(), model=model))["answer"] == "되묻는 답"   # 조회 없음


def test_review_scope_skips_first_turn_answers_built_on_a_domain_tool(monkeypatch, tmp_path):
    # 분야 도구(building_basis)가 근거를 짜 준 첫 턴 답은 점검하지 않는다 — 이어지는 질문, 일반 법령 질문만 점검
    d = tmp_path / "domains"
    d.mkdir()
    (d / "건축개발.txt").write_text("법령: 건축법\n도구: building_basis\n---\n지침", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "domains_dir", "domains")
    monkeypatch.setattr(settings, "answer_review_scope", "follow_up_or_general")
    basis = [{"tool": "building_basis", "args": {"use": "공장"}}]
    article = [{"tool": "law_article", "args": {"law": "건축법", "article": "제44조"}}]
    assert not loop.review_wanted(False, basis)
    assert loop.review_wanted(True, basis)
    assert loop.review_wanted(False, article)
    monkeypatch.setattr(settings, "answer_review_scope", "all")
    assert loop.review_wanted(False, basis)
