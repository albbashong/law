"""조건식 대조 — 답과 조회 원문의 "수치·단위·비교어"를 (값, 단위) → 부호(방향 × 효과)로 바꿔 맞춰 본다.
방향은 비교어(이상·초과·넘는 = 위, 이하·미만·못 미치는 = 아래), 효과는 그 항목과 딸린 윗단 문장의 부정(없다·외에는·
제외·금지·아니…) 개수의 홀짝이다. "2천㎡ 이하면 분할할 수 없다"는 "2천㎡를 넘으면 분할할 수 있다"와 같은 부호다.
답의 조건이 같은 값의 원문 조건 어느 것과도 부호가 맞지 않으면 거꾸로 쓴 것으로 본다."""
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


def test_source_condition_inherits_the_lead_sentence():
    conds = conditions.parse(FARM)
    c = next(c for c in conds if c.value == 2000)
    assert c.unit == "㎡" and c.sign == 1          # 넘도록(위) × "외에는 … 없다"(부정 둘 → 긍정)


def test_reversed_threshold_in_a_list_is_flagged():
    # 실측 W7 3회째 답: "다음 중 하나라도 해당하면 분할이 가능 … 3) 2,000㎡ 이하인 경우"
    answer = ("* 농업생산기반정비사업이 시행된 농지는 다음 중 **하나라도 해당하면** 분할이 **가능**합니다.\n"
              "  1) 도시·군계획시설 부지에 포함된 경우\n"
              "  3) 분할 후 각 필지 면적이 **2,000㎡ 이하**인 경우\n")
    found = conditions.reversed_conditions(answer, TOOL)
    assert len(found) == 1 and "2,000㎡ 이하" in found[0] and "2천제곱미터를 넘도록" in found[0]


def test_same_meaning_with_both_flipped_passes():
    for ok in ("각 필지 면적이 2,000㎡를 초과하면 분할할 수 있습니다.",
               "각 필지 면적이 2,000㎡ 이하가 되도록은 분할할 수 없습니다.",
               "| 농지법 | 제22조제2항 | 다음 경우 외에는 분할할 수 없음: ① 도시지역 ③ 각 필지 면적이 2,000 ㎡ 초과인 경우 |"):
        assert conditions.reversed_conditions(ok, TOOL) == [], ok


def test_values_the_sources_do_not_compare_are_not_judged():
    assert conditions.reversed_conditions("면적이 500㎡ 이하이면 분할할 수 없습니다.", TOOL) == []
    assert conditions.reversed_conditions("면적이 2,000㎡ 이하입니다.", [{"role": "user", "content": FARM}]) == []


def test_any_agreeing_source_occurrence_clears_it():
    other = [{"role": "tool", "content": "60제곱미터 미만으로 분할할 수 없다."},
             {"role": "tool", "content": "다음 각 호의 행위는 허가를 받아야 한다. 1. 60제곱미터 미만으로의 분할"}]
    assert conditions.reversed_conditions("60㎡ 미만으로의 분할은 허가를 받아야 합니다.", other) == []
    assert conditions.reversed_conditions("60㎡ 미만으로 분할할 수 없습니다.", other) == []
    # 같은 값에 효과의 종류가 다른 원문(분할 불가 / 허가 필요)이 섞이면 부호가 엇갈려 판단하지 않는다(오탐 방지)
    assert conditions.reversed_conditions("60㎡ 이상이면 분할할 수 없습니다.", other) == []
    assert conditions.reversed_conditions("60㎡ 이상이면 분할할 수 없습니다.", other[:1]) != []


def test_number_forms_are_normalized():
    assert {(c.value, c.unit) for c in conditions.parse("2천제곱미터를 넘도록, 1만 m² 이상, 1,500 ㎡ 미만, 30퍼센트 이하")} \
        == {(2000, "㎡"), (10000, "㎡"), (1500, "㎡"), (30, "%")}


class Mcp:
    async def list_tools(self):
        return [{"name": "split_area", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"지목_분할제한": [FARM]}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


LOOKUP = {"tool": "split_area", "args": {"land_category": "농지"}}


async def test_reversed_answer_is_sent_back_once_with_the_conditions(monkeypatch):
    import loop
    from config import settings
    monkeypatch.setattr(settings, "answer_condition_check", True)
    wrong = {"answer": "각 필지 면적이 2,000㎡ 이하인 경우 분할할 수 있습니다."}
    right = {"answer": "각 필지 면적이 2,000㎡를 넘도록 분할하는 경우 분할할 수 있습니다."}
    model = Model([LOOKUP, wrong, right])
    raw = await loop.run_query("경지정리 농지 분할?", mcp=Mcp(), model=model)
    assert raw["answer"] == right["answer"]
    note = model.seen[-1][-1]["content"]
    assert "2,000㎡ 이하" in note and "2천제곱미터를 넘도록" in note


async def test_check_is_off_without_the_setting():
    import loop
    model = Model([LOOKUP, {"answer": "각 필지 면적이 2,000㎡ 이하인 경우 분할할 수 있습니다."}])
    raw = await loop.run_query("q", mcp=Mcp(), model=model)
    assert "이하" in raw["answer"] and len(model.seen) == 2


# 실측 양평군 도시계획 조례 제22조① — 앞쪽 적용 범위에 부정(받지 아니하거나·불가능한)이 셋 있지만 효과는 "제한할 수 있다"
ORDIN = {"조문": "양평군 도시계획 조례 제22조제1항제1호", "제목": "토지분할제한", "딸린_문장": [
    "제22조제1항: ① 군수는 관계 법령에 따른 허가·인가 등을 받지 아니하거나, 기반시설이 갖추어지지 아니하여 토지의 개발이 "
    "불가능한 토지를 분할하는 경우로서 다음 각 호 중 어느 하나에 해당하는 경우에는 토지분할을 제한할 수 있다."],
    "해당": "1. 녹지지역으로서 분할하려는 토지의 면적이 200제곱미터 미만인 경우 (전문개정 2014. 10. 31)"}


def test_scope_negations_before_the_list_are_not_the_effect():
    msgs = [{"role": "tool", "content": json.dumps({"facts": {"토지분할_허가기준": [ORDIN]}}, ensure_ascii=False)}]
    for ok in ("200 ㎡ 미만이면 분할할 수 없습니다.", "면적이 200 ㎡ 이상이면 분할이 허용될 수 있습니다.",
               "녹지지역에서 200 ㎡ 미만이면 분할이 제한됩니다."):
        assert conditions.reversed_conditions(ok, msgs) == [], ok
    assert conditions.reversed_conditions("200 ㎡ 이상이면 분할할 수 없습니다.", msgs) != []


def test_polite_negation_endings_count():
    # 실측 v4 W6: "200 ㎡ 미만이므로 분할이 허용되지 않습니다"가 부정으로 안 읽혀 거꾸로라고 걸렸다
    msgs = [{"role": "tool", "content": json.dumps({"facts": {"토지분할_허가기준": [ORDIN]}}, ensure_ascii=False)}]
    assert conditions.reversed_conditions("200 ㎡ 미만이므로 분할이 허용되지 않습니다.", msgs) == []


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
