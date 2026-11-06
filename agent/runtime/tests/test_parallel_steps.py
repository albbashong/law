"""서로의 결과를 쓰지 않는 모델 호출은 함께 부른다 — 값 뽑기와 이야기, 설명틀 고르기와 이야기 계획.
실측 2026-11-05: 한 질문 66초 중 62초가 모델 호출이었고 모두 차례로 기다렸다. vLLM 은 동시 요청을 묶어
처리하므로 함께 부르면 그만큼 기다림이 준다."""
import asyncio

import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"refs": [{"law": "법", "article": "제25조"}],
                                          "구조": [{"조항": "제25조제1항", "관계": "각 호 중 하나만 해당하면 된다"}]}}


class Model:
    """도구 단계는 script 순서로, 그 밖의 단계는 시스템 지시로 답한다. 함께 떠 있던 지시들을 기록한다."""

    def __init__(self, script, by_prompt):
        self.script, self.by_prompt = list(script), by_prompt
        self.flying, self.together, self.seen = set(), [], {}

    async def next_step(self, messages, tools):
        system = messages[0]["content"] if messages[0]["role"] == "system" else ""
        key = next((k for k in self.by_prompt if system.startswith(k)), None)
        if key is None:
            return self.script.pop(0)
        self.seen[key] = messages
        self.flying.add(key)
        self.together.append(set(self.flying))
        await asyncio.sleep(0.01)
        self.flying.discard(key)
        return {"answer": self.by_prompt[key]}


STORY = {"tool": loop.STORY, "args": {}}
LOOKUP = {"tool": "law_article", "args": {"law": "법", "article": "제25조"}}
ANSWER = {"answer": "제25조가 정합니다."}


def _on(monkeypatch, tmp_path):
    for name, text in {"story.txt": "이야기로 쓰세요. 유형: {설명틀}", "param.txt": "값을 뽑으세요.",
                       "plan.txt": "계획을 JSON 으로.", "select.txt": "틀 하나만 답하세요.\n{설명틀_목록}"}.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    forms = tmp_path / "forms"
    forms.mkdir()
    (forms / "택일형.txt").write_text("어느 하나에 해당하면 되는 조문\n한 길로 통과한다.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조")
    monkeypatch.setattr(settings, "story_prompt", "story.txt")
    monkeypatch.setattr(settings, "param_prompt", "param.txt")
    monkeypatch.setattr(settings, "story_plan_prompt", "plan.txt")
    monkeypatch.setattr(settings, "story_plan_grounded_keys", "관계")
    monkeypatch.setattr(settings, "story_form_select_prompt", "select.txt")
    monkeypatch.setattr(settings, "story_forms_dir", "forms")


BY_PROMPT = {"값을": "[]", "틀 하나만": "택일형",
             "계획을": '{"사유": "제25조제1항", "관계": "각 호 중 하나만 해당하면 된다"}',
             "이야기로": "예를 들어 볼게요. 제25조에 따르면 할 수 있어요."}


async def test_values_and_story_are_asked_together(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    model = Model([STORY, LOOKUP, ANSWER], BY_PROMPT)
    raw = await loop.run_query("예시 줘봐", mcp=Mcp(), model=model)
    assert "예를 들어 볼게요." in raw["answer"]
    assert any({"값을", "틀 하나만"} <= s for s in model.together)


async def test_form_and_plan_are_asked_together_and_both_are_used(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "param_prompt", "")
    model = Model([STORY, LOOKUP, ANSWER], BY_PROMPT)
    await loop.run_query("예시 줘봐", mcp=Mcp(), model=model)
    assert any({"틀 하나만", "계획을"} <= s for s in model.together)
    # 이야기는 계획을 받고, 고른 틀로 쓴다 — 함께 불러도 둘 다 반영된다
    story = model.seen["이야기로"]
    assert "유형: 택일형" in story[0]["content"] and "각 호 중 하나만 해당하면 된다" in story[1]["content"]
