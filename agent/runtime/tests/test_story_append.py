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


def test_story_narrowing_an_article_of_the_answer_passes(monkeypatch):
    # 실측 P03·W7: 답은 "제14조 제1항"·"제22조제2항"까지만 적었고 이야기는 그 아래 호를 짚었다 — 좁혀 쓴 것이지 다른 조문이 아니다
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?")
    answer = "건축법 제14조 제1항에 따라 신고하면 됩니다. 농지법 제22조제2항(세분화 방지)."
    assert loop.beyond_answer("예를 들어 볼게요. 제14조제1항제2호에 해당해요. 제22조제2항 제3호예요.", answer) == []
    assert loop.beyond_answer("예를 들어 볼게요. 제35조에 따라 신고해요.", answer) != []      # 답에 없는 조


def test_story_naming_a_sibling_of_what_the_answer_named_is_flagged(monkeypatch):
    # 답이 "별표 10 제2호 사목"을 적었는데 이야기가 같은 호의 다른 목(라목)을 붙이면 좁혀 쓴 것이 아니다
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?")
    assert loop.beyond_answer("예를 들어 볼게요. 지을 수 있어요(별표 10 제2호 라목).", ANSWER) != []


def test_sentence_using_only_shared_words_is_not_flagged():
    # 실측 P08: "건폐율 20% 이하(제56조제18호)"는 맞는 조문인데, 그 행에만 있는 말(성장관리계획…)이 없다고 걸렸다 —
    # 다른 행에만 있는 말을 담았을 때만(내용이 다른 행의 것) 어긋난 것으로 본다
    answer = """| 법령 | 조문 | 핵심 |
|---|---|---|
| 영천시 도시계획 조례 | 제56조제18호 | 생산관리지역 건폐율 20% 이하(성장관리계획을 수립한 지역은 30% 이하) |
| 시행령 | 제84조제1항제18호 | 생산관리지역 건폐율 20% 이하(조례와 동일) |
| 시행령 | 별표 1 제1호 | 단독주택(가정어린이집·공동생활가정 포함) |"""
    story = ("예를 들어 볼게요. 건폐율이 20% 이하여야 해요(영천시 도시계획 조례 제56조제18호).\n"
             "민준이는 생산관리지역에 단독주택(별표 1 제1호)을 지으려 해요.")
    assert loop.misattributed(story, answer) == []


def test_word_with_a_particle_in_another_row_is_not_that_rows_own():
    # 실측 P08: 한 행은 "단독주택이", 다른 행은 "단독주택" — 조사만 다른 같은 말을 한 행의 말로 보면 맞는 문장이 걸린다
    answer = """| 법령 | 조문 | 핵심 |
|---|---|---|
| 시행령 | 별표 19 | 생산관리지역 안에서 건축할 수 있는 건축물에 단독주택이 포함됨 |
| 건축법 시행령 | 별표 1 제1호 | "단독주택"을 건축물 용도 중 하나로 규정 |"""
    story = "예를 들어 볼게요. 별표 1 제1호의 단독주택이라고 적혀 있어요(국토계획법 시행령 별표 19)."
    assert loop.misattributed(story, answer) == []


def test_law_names_inside_a_rows_content_are_not_its_own_words():
    # 실측 P08: 내용 칸이 "건축법 시행령 별표 1 제1호(단독주택)"을 인용 — "건축법"이 그 행의 말로 잡혀, 이야기 문장의
    # 인용 "(건축법 시행령 별표 1 제1호)"이 다른 행의 말을 담은 것으로 걸렸다
    answer = """| 법령 | 조문 | 핵심 |
|---|---|---|
| 국토의 계획 및 이용에 관한 법률 시행령 | 별표 19 제1호 가목 | 생산관리지역에서 건축할 수 있는 건축물: 건축법 시행령 별표 1 제1호(단독주택) |
| 건축법 시행령 | 별표 1 제1호 | 단독주택(가정어린이집·공동생활가정 포함) |
| 영천시 도시계획조례 | 제56조 제18호 | 생산관리지역 건폐율: 20 % 이하 |"""
    story = "예를 들어 볼게요. 민수가 짓고 싶은 건물은 단독주택(건축법 시행령 별표 1 제1호)이에요."
    assert loop.misattributed(story, answer) == []



def _unverified_on(monkeypatch, tmp_path, avoid="[확인 안 된 인용] 이야기에 쓰지 마세요: {cites}"):
    # 실측 2026-10-29 probe7 3차: 사실 답에 "원문 확인 필요" 인용이 남으면 예시를 아예 만들지 않아 13건 중 6건이 빠졌다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    monkeypatch.setattr(settings, "answer_unverified_text", "\n\n※ 확인 필요: {cites}")
    monkeypatch.setattr(settings, "story_unverified_text", avoid)


async def test_answer_with_unverified_cites_still_gets_a_story_told_to_avoid_them(monkeypatch, tmp_path):
    _unverified_on(monkeypatch, tmp_path)
    fact = FACT + "\n제99조도 봐야 해요."
    story = "예를 들어 볼게요. 근린상업지역이면 제25조를 봐요."
    model = Model([CALL, LOOKUP, {"answer": fact}, {"answer": fact}, {"answer": story}])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == fact + "\n\n※ 확인 필요: 제26조, 제99조" + "\n\n---\n\n" + story
    request = model.seen[-1][1]["content"]
    assert "이야기에 쓰지 마세요: 제26조, 제99조" in request and "※ 확인 필요" not in request


async def test_story_reusing_an_unverified_cite_is_dropped(monkeypatch, tmp_path):
    _unverified_on(monkeypatch, tmp_path)
    fact = FACT + "\n제99조도 봐야 해요."
    story = {"answer": "예를 들어 볼게요. 근린상업지역이면 제99조를 봐요."}
    model = Model([CALL, LOOKUP, {"answer": fact}, {"answer": fact}, story, story])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == fact + "\n\n※ 확인 필요: 제26조, 제99조"


async def test_without_the_avoid_text_an_unverified_answer_gets_no_story(monkeypatch, tmp_path):
    _unverified_on(monkeypatch, tmp_path, avoid="")
    fact = FACT + "\n제99조도 봐야 해요."
    model = Model([CALL, LOOKUP, {"answer": fact}, {"answer": fact}])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == fact + "\n\n※ 확인 필요: 제26조, 제99조"


async def test_story_naming_a_law_absent_from_the_lookups_is_rewritten(monkeypatch, tmp_path):
    # 이야기도 답과 같은 법령명 검사를 거친다 — 실측 W2·W7: 답의 표에 조회에 없는 "건축법 시행령"이 남았다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_law_pattern", r"「([^」\n]+)」")
    bad = {"answer": "예를 들어 볼게요. 근린상업지역이면 「건축법 시행령」 제25조를 봐요."}
    good = "예를 들어 볼게요. 근린상업지역이면 제25조를 봐요."
    model = Model([CALL, LOOKUP, {"answer": FACT}, bad, {"answer": good}])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == FACT + "\n\n---\n\n" + good


def _review_on(monkeypatch, tmp_path, verdict):
    _on(monkeypatch, tmp_path)
    (tmp_path / "review.txt").write_text("점검하세요.", encoding="utf-8")
    monkeypatch.setattr(settings, "story_review_prompt", "review.txt")
    monkeypatch.setattr(settings, "story_review_hint_text", "\n\n[의심]\n{hints}")
    return {"answer": verdict}


async def test_misattribution_goes_to_the_review_as_a_hint_and_the_review_decides(monkeypatch, tmp_path):
    # 실측 2026-10-29 probe7: 조문-내용 짝 검사가 맞는 이야기 4건을 버렸다(한 문장에 조문 둘, 나열, 바꿔 쓴 말) —
    # 점검이 있으면 바로 버리지 않고 의심으로 넘겨, 점검이 문제없다고 하면 이야기를 낸다
    ok = _review_on(monkeypatch, tmp_path, '{"문제": []}')
    story = "예를 들어 볼게요. 생산관리지역이면 제25조를 봐요."
    model = Model([CALL, LOOKUP, {"answer": FACT}, {"answer": story}, ok])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == FACT + "\n\n---\n\n" + story
    review_request = model.seen[-1][1]["content"]
    assert "[의심]" in review_request and "근린상업지역" in review_request


async def test_review_confirming_the_hint_rewrites_the_story(monkeypatch, tmp_path):
    no = _review_on(monkeypatch, tmp_path, '{"문제": ["생산관리지역 문장의 조문이 틀림"]}')
    story = {"answer": "예를 들어 볼게요. 생산관리지역이면 제25조를 봐요."}
    model = Model([CALL, LOOKUP, {"answer": FACT}, story, no, story, no])
    raw = await loop.run_query("예시 줘", mcp=Mcp(), model=model)
    assert raw["answer"] == FACT
