"""이야기는 사실 답 아래에 덧붙인다(STORY_APPEND_FORMAT) — 이야기가 틀려도 사실 답은 남는다. 이야기가 문장마다 붙인 조문은
사실 답의 근거표에서 그 조문 행에만 있는 말(다른 행에 없는 말)이 그 문장에 하나는 있어야 한다. 실측 2026-10-29 W2: 이야기가
"생산관리지역이면 창고를 지을 수 있다(별표 10 제2호 라목)"라고 썼는데, 답의 근거표에서 별표 10 행은 근린상업지역 행이었다
(생산관리지역은 별표 19). 법령·조문 짝 검사로는 잡히지 않는다."""
import loop
from config import settings

ANSWER = """창고 허용 용도지역입니다.

| 법령 | 조문 | 핵심 내용 |
|------|------|-----------|
| 시행령 | 별표 10 제2호 사목 | 근린상업지역 — 조례로 허용 가능 |
| 시행령 | 별표 19 제1호 마목 | 생산관리지역 — 시행령으로 허용 |"""


def test_sentence_citing_another_rows_article_is_flagged():
    story = "예를 들어 볼게요. 땅이 생산관리지역이면 창고를 지을 수 있어요(시행령 별표 10 제2호 사목)."
    problems = loop.misattributed(story, ANSWER)
    assert len(problems) == 1 and "별표 10 제2호 사목" in problems[0] and "근린상업지역" in problems[0]


def test_sentence_matching_its_row_passes():
    story = ("예를 들어 볼게요. 땅이 생산관리지역이면 창고를 지을 수 있어요(별표 19 제1호 마목).\n"
             "근린상업지역이면 조례를 봐야 해요(별표 10 제2호 사목).")
    assert loop.misattributed(story, ANSWER) == []


def test_no_table_or_no_distinct_words_checks_nothing():
    assert loop.misattributed("예를 들어 볼게요. 별표 10 제2호 사목에 따라.", "표 없는 답") == []
    same = ANSWER.replace("근린상업지역 — 조례로 허용 가능", "생산관리지역 — 시행령으로 허용")
    assert loop.misattributed("예를 들어 볼게요. 무엇이든(별표 10 제2호 사목).", same) == []


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "조문", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}], "원문": "근린상업지역 생산관리지역"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


def _on(monkeypatch, tmp_path):
    (tmp_path / "story.txt").write_text("이야기로.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "story_prompt", "story.txt")
    monkeypatch.setattr(settings, "story_append_format", "{answer}\n\n---\n\n{story}")


FACT = "제25조: 근린상업지역 조례.\n\n| 법령 | 조문 | 핵심 |\n|---|---|---|\n| 법 | 제25조 | 근린상업지역 |\n| 법 | 제26조 | 생산관리지역 |"
CALL = {"tool": loop.STORY, "args": {}}
LOOKUP = {"tool": "law_article", "args": {"law": "법", "article": "제25조"}}


async def test_story_is_appended_below_the_factual_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([CALL, LOOKUP, {"answer": FACT}, {"answer": "예를 들어 볼게요. 근린상업지역이면 제25조를 봐요."}])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == FACT + "\n\n---\n\n예를 들어 볼게요. 근린상업지역이면 제25조를 봐요."


async def test_misattributed_story_is_rewritten_then_dropped(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    bad = {"answer": "예를 들어 볼게요. 생산관리지역이면 제25조를 봐요."}
    model = Model([CALL, LOOKUP, {"answer": FACT}, bad, bad])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == FACT                       # 두 번 다 걸리면 사실 답만
    assert "근린상업지역" in model.seen[-1][1]["content"]   # 다시 쓸 때 무엇이 틀렸는지 알려 준다


def test_story_citing_a_finer_article_than_the_answer_has_is_flagged(monkeypatch):
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?")
    # 실측 W2: 답에는 "별표 10 제2호 사목"뿐인데 이야기는 "별표 10 제2호 라목"을 붙였다(별표 10 은 답에 있어 인용 검사를 통과)
    story = "예를 들어 볼게요. 생산관리지역이면 지을 수 있어요(별표 10 제2호 라목)."
    problems = loop.beyond_answer(story, ANSWER)
    assert len(problems) == 1 and "별표 10 제2호 라목" in problems[0]
    assert loop.beyond_answer("예를 들어 볼게요. 별표 19 제1호 마목, 별표 10 에 따라.", ANSWER) == []
