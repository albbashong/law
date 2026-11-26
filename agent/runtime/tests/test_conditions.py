"""조건 대조 — 답과 조회 원문이 같은 (값, 단위)를 서로 다른 비교어(이상·초과 ↔ 이하·미만)로 적은 곳을 찾아 답 점검에
넘긴다. 런타임은 뜻을 판정하지 않는다: "2천㎡ 이하면 분할할 수 없다"와 "2천㎡를 넘으면 분할할 수 있다"가 같은 뜻인지는
부정 낱말 목록을 세어서가 아니라 점검 단계가 원문을 보고 가린다(전수조사 H1·H2 — 일상어 낱말 목록으로 효과를 판정하지 않는다)."""
import json

import conditions

# 실측 2026-10-29 W7 — 분할 도구가 준 농지법 제22조② (지목_분할제한)
FARM = ("농지법 제22조제2항(농지 소유의 세분화 방지): ②「농어촌정비법」에 따른 농업생산기반정비사업이 시행된 농지는 "
        "다음 각 호의 어느 하나에 해당하는 경우 외에는 분할할 수 없다. <개정 2009.5.27, 2011.4.14> 1. 「국토의 계획 및 "
        "이용에 관한 법률」에 따른 도시지역의 주거지역ㆍ상업지역ㆍ공업지역 또는 도시ㆍ군계획시설부지에 포함되어 있는 농지를 "
        "분할하는 경우 2. 제34조제1항에 따라 농지전용허가를 받거나 농지전용신고를 하고 전용한 농지를 분할하는 경우 "
        "3. 분할 후의 각 필지의 면적이 2천제곱미터를 넘도록 분할하는 경우 4. 농지의 개량, 농지의 교환ㆍ분합 등 대통령령으로 "
        "정하는 사유로 분할하는 경우")
TOOL = [{"role": "tool", "content": json.dumps({"facts": {"지목_분할제한": [FARM]}}, ensure_ascii=False)}]


def test_number_forms_are_normalized():
    assert {(c.value, c.unit) for c in conditions.parse("2천제곱미터를 넘도록, 1만 m² 이상, 1,500 ㎡ 미만, 30퍼센트 이하")} \
        == {(2000, "㎡"), (10000, "㎡"), (1500, "㎡"), (30, "%")}


def test_same_value_with_the_opposite_comparator_is_a_suspect():
    out = conditions.suspects("각 필지 면적이 2,000㎡ 이하인 경우 분할할 수 있습니다.", TOOL)
    assert len(out) == 1 and "2,000㎡ 이하" in out[0] and "2천제곱미터를 넘도록" in out[0]


def test_same_comparator_is_not_a_suspect():
    assert conditions.suspects("각 필지가 2,000㎡를 초과하도록 분할하는 경우입니다.", TOOL) == []


def test_values_the_sources_do_not_compare_are_not_suspects():
    assert conditions.suspects("면적이 500㎡ 이하이면 됩니다.", TOOL) == []


def test_any_source_with_the_same_comparator_clears_it():
    both = TOOL + [{"role": "tool", "content": json.dumps({"facts": {"원문": "2천제곱미터 이하인 농지는 제외한다."}},
                                                         ensure_ascii=False)}]
    assert conditions.suspects("2,000㎡ 이하인 경우", both) == []


def test_meaning_is_not_judged_by_negation_words():
    # 비교어가 다르면 뜻이 같아 보여도 후보로만 올린다 — 같은 뜻인지는 점검 단계가 원문으로 가린다
    assert conditions.suspects("2,000㎡ 이하이면 분할할 수 없습니다.", TOOL) != []
    assert not hasattr(conditions, "NEGATION") and not hasattr(conditions, "EFFECT")


class Mcp:
    async def list_tools(self):
        return [{"name": "split_area", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"지목_분할제한": [FARM]}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LOOKUP = {"tool": "split_area", "args": {"land_category": "농지"}}


async def test_without_a_review_step_nothing_is_sent_back(monkeypatch):
    # 점검 단계가 없으면 런타임이 혼자 판정해 되돌리지 않는다
    import loop
    from config import settings
    monkeypatch.setattr(settings, "answer_condition_check", True)
    model = Model([LOOKUP, {"answer": "각 필지 면적이 2,000㎡ 이하인 경우 분할할 수 있습니다."}])
    raw = await loop.run_query("경지정리 농지 분할?", mcp=Mcp(), model=model)
    assert "이하" in raw["answer"] and len(model.seen) == 2


async def test_with_review_on_the_suspected_conditions_go_to_the_review(monkeypatch, tmp_path):
    import loop
    from config import settings
    (tmp_path / "review.txt").write_text("점검하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "answer_review_prompt", "review.txt")
    monkeypatch.setattr(settings, "answer_review_scope", "all")
    monkeypatch.setattr(settings, "answer_condition_check", True)
    wrong = {"answer": "각 필지 면적이 2,000㎡ 이하인 경우 분할할 수 있습니다."}
    model = Model([LOOKUP, wrong, {"answer": '{"문제": []}'}])        # 점검이 문제 없다고 하면 그대로 낸다
    raw = await loop.run_query("q", mcp=Mcp(), model=model)
    review = model.seen[-1][1]["content"]
    assert raw["answer"] == wrong["answer"] and "의심한 조건" in review and "2천제곱미터를 넘도록" in review
