"""도구가 후보 중 고르라고 돌려준 결과(needs_disambiguation) 뒤에 모델이 글로 답하면 — 런타임이 그 후보로 사용자에게
되묻는다(ask). 모델이 다른 조회로 풀었으면(그 뒤에 성공한 결과가 있으면) 그대로 답한다."""
import loop

CANDS = [{"id": "1", "label": "건축법"}, {"id": "2", "label": "건축법 시행령"}]


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "조문", "input_schema": {}}]

    async def call(self, name, args):
        if args.get("law") == "건축":
            return {"status": "needs_disambiguation", "facts": {"candidates": CANDS, "대상": "법령"}, "message": "고르세요"}
        return {"status": "ok", "facts": {"법령": "건축법", "조문": "제11조", "원문": "허가"}}


class Model:
    def __init__(self, script):
        self.script = list(script)

    async def next_step(self, messages, tools, require=False):
        return self.script.pop(0)


def _call(law):
    return {"tools": [{"id": "c", "name": "law_article", "args": {"law": law, "article": "제11조"}}]}


async def test_text_answer_after_candidates_becomes_a_question_with_them():
    raw = await loop.run_query("건축 제11조?", mcp=Mcp(), model=Model([_call("건축"), {"answer": "어느 법령인가요?"}]))
    assert raw["stopped"] == "ask"
    assert [c["label"] for c in raw["ask"]["candidates"]] == ["건축법", "건축법 시행령"]
    assert raw["ask"]["question"] == "어느 법령인가요?"


async def test_candidates_resolved_by_a_later_lookup_answer_as_usual():
    raw = await loop.run_query("건축 제11조?", mcp=Mcp(),
                               model=Model([_call("건축"), _call("건축법"), {"answer": "허가를 받아야 합니다."}]))
    assert raw["stopped"] != "ask" and raw["answer"].startswith("허가를 받아야 합니다.")
