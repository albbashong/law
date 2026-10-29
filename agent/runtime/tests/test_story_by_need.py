"""질문 정리 단계가 예시가 필요한 질문이라고 판단하면(RESTATE_STORY_KEY 칸이 참), 사용자가 예시를 달라고
하지 않아도 검증된 답을 이야기로 다시 쓴다. 판단은 정리 단계의 모델이 질문의 성격으로 한다(낱말 목록 없음)."""
import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}]}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LOOKUP = {"tool": "law_article", "args": {"law": "법", "article": "제25조"}}
STORY = "예를 들어 볼게요. 제25조에 따르면 할 수 있어요."


def _on(monkeypatch, tmp_path, key="예시"):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    (tmp_path / "story.txt").write_text("이야기로 다시 쓰세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "restate_story_key", key)
    monkeypatch.setattr(settings, "story_prompt", "story.txt")
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")


async def test_question_needing_an_example_is_retold_without_being_asked(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "음식물 제공 가액 기준", "짐작": "", "예시": true}'},
                   LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": STORY}])
    raw = await loop.run_query("공무원한테 밥 사도 돼?", mcp=Mcp(), model=model)
    assert raw["answer"] == STORY
    assert model.seen[-1][0] == {"role": "system", "content": "이야기로 다시 쓰세요."}


async def test_question_not_needing_an_example_keeps_the_factual_answer(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([{"answer": '{"질문": "제25조 원문", "짐작": "", "예시": false}'},
                   LOOKUP, {"answer": "제25조가 정합니다."}])
    raw = await loop.run_query("제25조 원문 보여줘", mcp=Mcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다."


async def test_judgement_is_ignored_without_the_key(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, key="")
    model = Model([{"answer": '{"질문": "음식물 제공 가액 기준", "짐작": "", "예시": true}'},
                   LOOKUP, {"answer": "제25조가 정합니다."}])
    raw = await loop.run_query("공무원한테 밥 사도 돼?", mcp=Mcp(), model=model)
    assert raw["answer"] == "제25조가 정합니다."


async def test_judged_example_does_not_replay_the_previous_turn(monkeypatch, tmp_path):
    # 앞 턴 조회를 다시 실행하는 건 "앞 답을 이야기로" 요청(retell_as_story)일 때만이다
    _on(monkeypatch, tmp_path)
    calls = []

    class Spy(Mcp):
        async def call(self, name, args):
            calls.append(args)
            return await super().call(name, args)

    context = {"memory": {"turns": [{"q": "앞 질문", "a": "앞 답", "calls": [
        {"tool": "law_article", "args": {"law": "다른법", "article": "제1조"}}]}]}}
    model = Model([{"answer": '{"질문": "음식물 제공 가액 기준", "짐작": "", "예시": true}'},
                   LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": STORY}])
    await loop.run_query("공무원한테 밥 사도 돼?", mcp=Spy(), model=model, context=context)
    assert {"law": "다른법", "article": "제1조"} not in calls


async def test_follow_up_keeps_the_factual_answer(monkeypatch, tmp_path):
    # 실측 2026-10-29 W6: "최소필지분할면적은?"(앞 질문에 이어짐)이 지어낸 면적의 예시 이야기로만 나갔다 — 앞 턴이 이미
    # 예시를 줬고 이어지는 질문은 앞 답에서 좁혀 묻는 것이라, 이어짐이면 판단만으로 이야기로 바꾸지 않는다
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "restate_follow_key", "이어짐")
    context = {"memory": {"turns": [{"q": "다문리 160 분할 가능해?", "a": "앞 답", "calls": []}]}}
    model = Model([{"answer": '{"질문": "다문리 160 최소분할면적", "짐작": "", "예시": true, "이어짐": true}'},
                   LOOKUP, {"answer": "제25조가 정합니다."}, {"answer": STORY}])
    raw = await loop.run_query("최소필지분할면적은?", mcp=Mcp(), model=model, context=context)
    assert raw["answer"] == "제25조가 정합니다."
