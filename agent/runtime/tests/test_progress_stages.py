"""진행 알림 — 도구 이름(split_area 실행 중) 하나만 보내지 않고, 단계마다 사용자 말로 무엇을 하는지 알린다:
질문 정리 → 조회(무엇을, 핵심 인자와 함께) → 답 점검 → 예시 → 값 뽑기. 도구 표시 이름은 설정(PROGRESS_TOOL_LABELS)이 정한다."""
import loop
from config import settings


class Mcp:
    async def list_tools(self):
        return [{"name": "split_area", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"용도지역별": [{"구분": "녹지지역: 200제곱미터"}]}}


class Model:
    def __init__(self, script):
        self.script = list(script)

    async def next_step(self, messages, tools):
        return self.script.pop(0)


def _on(monkeypatch, tmp_path):
    for f, text in (("restate.txt", "정리"), ("review.txt", "점검"), ("param.txt", "값")):
        (tmp_path / f).write_text(text, encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "answer_review_prompt", "review.txt")
    monkeypatch.setattr(settings, "answer_review_scope", "all")
    monkeypatch.setattr(settings, "param_prompt", "param.txt")
    monkeypatch.setattr(settings, "progress_tool_labels", '{"split_area": "분할 제한면적을 찾는 중"}')


async def test_each_stage_is_announced_in_plain_words(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    seen = []
    model = Model([{"answer": '{"질문": "다문리 160 최소분할면적", "짐작": ""}'},
                   {"tool": "split_area", "args": {"address": "다문리 160", "zone": None}},
                   {"answer": "녹지지역은 200제곱미터입니다."}, {"answer": '{"문제": []}'}, {"answer": "[]"}])
    await loop.run_query("다문리 160 최소분할면적?", mcp=Mcp(), model=model,
                         on_progress=lambda f, m: seen.append(m))
    text = " / ".join(seen)
    assert seen[0] == settings.progress_restate_text
    assert any("분할 제한면적" in m and "다문리 160" in m and "split_area" not in m for m in seen), text
    assert settings.progress_review_text in seen and settings.progress_params_text in seen
    assert seen.index(settings.progress_review_text) < seen.index(settings.progress_params_text)


async def test_unknown_tool_falls_back_to_its_name(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "progress_tool_labels", "")
    seen = []
    model = Model([{"answer": '{"질문": "q", "짐작": ""}'}, {"tool": "split_area", "args": {}},
                   {"answer": "답"}, {"answer": '{"문제": []}'}, {"answer": "[]"}])
    await loop.run_query("q", mcp=Mcp(), model=model, on_progress=lambda f, m: seen.append(m))
    assert any("split_area" in m for m in seen)


def test_only_the_listed_arguments_are_shown(monkeypatch):
    monkeypatch.setattr(settings, "progress_tool_labels", '{"law_article": "조문 원문을 여는 중"}')
    monkeypatch.setattr(settings, "progress_arg_keys", "law,article")
    text = loop.tool_progress("law_article", {"law": "건축법", "article": "제57조", "chain": "up", "depth": 2})
    assert text == "조문 원문을 여는 중 — 건축법, 제57조"
