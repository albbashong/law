"""답 검증 넓히기 — 비율은 답이 인용한 조문 원문하고만 대조, 답에 쓴 법령명은 조회 결과에 있어야 함,
도구 호출 표기(【도구 {...}】)는 답에서 지운다."""
import json

import loop
from config import settings

PAIR = r"(?P<scope>[^|\n]*?)\s*\|?\s*(?P<cite>제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?)"
LAW = r"(?m)「([^」\n]+)」|‘([^’\n]{2,}?(?:법|법률|령|규칙|조례|규정))’|^\|\s*\**([^|*\s][^|*\n]+?(?:법|법률|령|규칙|조례|규정))\**\s*\|"


def tool(facts):
    return {"role": "tool", "content": json.dumps({"status": "ok", "facts": facts}, ensure_ascii=False)}


def article(law, art, text):
    return tool({"법령": law, "조문": art, "원문": text, "호출인자": {"law": law, "article": art}})


def test_rates_are_checked_against_the_cited_article_only(monkeypatch):
    monkeypatch.setattr(settings, "answer_pair_pattern", PAIR)
    monkeypatch.setattr(settings, "answer_pair_keys", "law,article")
    msgs = [article("지방세법", "제11조", "1천분의 40"), article("지방세법", "제111조", "1천분의 10")]
    assert loop.rate_unmatched("지방세법 제11조에 따라 1%입니다.", msgs) == ["1%"]
    assert loop.rate_unmatched("지방세법 제111조에 따라 1%입니다.", msgs) == []


def test_rates_fall_back_to_all_sources_when_nothing_cited_matches(monkeypatch):
    monkeypatch.setattr(settings, "answer_pair_pattern", PAIR)
    monkeypatch.setattr(settings, "answer_pair_keys", "law,article")
    msgs = [article("지방세법", "제111조", "1천분의 10")]
    assert loop.rate_unmatched("세율은 1%입니다.", msgs) == []


def test_search_rows_are_scoped_one_row_at_a_time(monkeypatch):
    monkeypatch.setattr(settings, "answer_pair_pattern", PAIR)
    monkeypatch.setattr(settings, "answer_pair_keys", "law,article")
    rows = [{"law": "주택임대차보호법 시행령", "article": "제8조", "snippet": "1천분의 25"},
            {"law": "상가건물 임대차보호법 시행령", "article": "제4조", "snippet": "100분의 5"}]
    msgs = [tool({"결과": rows})]
    assert loop.rate_unmatched("주택임대차보호법 시행령 제8조: 2.5%, 상가는 5%", msgs) == ["5%"]


def test_law_names_missing_from_lookups_are_flagged(monkeypatch):
    monkeypatch.setattr(settings, "answer_law_pattern", LAW)
    msgs = [article("지방세법", "제111조", "1천분의 10")]
    answer = "| 법령 | 조문 |\n|---|---|\n| 재산세법 | 제3조 |\n| 지방세법 | 제111조 |\n「취득세법」에 따라"
    assert loop.unnamed_laws(answer, msgs) == ["재산세법", "취득세법"]
    assert loop.unnamed_laws("「지 방 세 법」 제111조", msgs) == []
    assert loop.unnamed_laws("| 법령 | 조문 |", msgs) == []    # 표 머리말은 법령명이 아니다


def test_law_name_check_is_off_without_a_pattern(monkeypatch):
    monkeypatch.setattr(settings, "answer_law_pattern", "")
    assert loop.unnamed_laws("「취득세법」", []) == []


def test_tool_call_notation_is_stripped(monkeypatch):
    monkeypatch.setattr(settings, "answer_strip_pattern", r"[^\S\n]*【[A-Za-z_][^】\n]*】")
    text = '5만원입니다【law_article {"law":"x","article":"별표 1"}】. 25,000원【law_article\u202ffacts:원문】. 【참고】 유지'
    assert loop.clean_answer(text) == "5만원입니다. 25,000원. 【참고】 유지"


QUOTE = r"“([^”\n]+)”|\"([^\"\n]+)\""


def test_quotes_must_appear_in_the_looked_up_text(monkeypatch):
    # 따옴표 인용문은 조회 원문에 그대로 있어야 한다(공백·문장부호·줄바꿈 무시, … 로 줄인 곳은 조각마다)
    monkeypatch.setattr(settings, "answer_quote_pattern", QUOTE)
    monkeypatch.setattr(settings, "answer_quote_min_chars", 6)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+")
    msgs = [article("야생생물 보호 및 관리에 관한 법률", "제23조의3",
                    "지방자치단체의 장은 유해야생동물에게\n먹이를 주는 행위를 금지할 수 있다.")]
    ok = "제23조의3 원문은 “유해야생동물에게 먹이를 주는 행위를 금지할 수 있다”고 정합니다."
    cut = "| 제23조의3 | “지방자치단체의 장은 … 금지할 수 있다” |"
    made = "제23조의3 원문은 “야생동물에게 먹이를 주는 행위는 금지한다”라고 합니다."
    short = "“먹이”를 주는 것"
    assert loop.misquoted(ok, msgs) == []
    assert loop.misquoted(cut, msgs) == []
    assert loop.misquoted(made, msgs) == ["야생동물에게 먹이를 주는 행위는 금지한다"]
    assert loop.misquoted(short, msgs) == []
    # 조문을 함께 적지 않은 줄의 따옴표는 원문 인용이 아니다(질문을 다시 적은 것 등)
    assert loop.misquoted("상황 – “공원에서 빵을 먹어도 되는지”를 묻는 질문입니다.", msgs) == []
    monkeypatch.setattr(settings, "answer_quote_pattern", "")
    assert loop.misquoted(made, msgs) == []


LOOSE = r"([^\S\n]*[가-힣A-Za-z0-9·ㆍ][가-힣A-Za-z0-9·ㆍ \t]{1,60}?(?:에 관한 (?:특별)?법률|에 관한 특별조치법|에 관한 법|법률|법)[^\S\n]+시행(?:령|규칙)|[^\S\n]*[가-힣A-Za-z0-9·ㆍ][가-힣A-Za-z0-9·ㆍ \t]{1,60}?에 관한 (?:특별)?법률|[^\S\n]*[가-힣A-Za-z0-9·ㆍ][가-힣A-Za-z0-9·ㆍ \t]{1,60}?에 관한 특별조치법)"


def test_unbracketed_full_law_names_must_end_with_a_looked_up_name(monkeypatch):
    # 「」 없이 늘여 쓴 정식명도 조회 결과의 법령명(법령·약칭 값)으로 끝나야 한다 — 지어낸 정식명을 막는다
    monkeypatch.setattr(settings, "answer_law_loose_pattern", LOOSE)
    monkeypatch.setattr(settings, "answer_law_keys", "법령,약칭")
    msgs = [tool({"법령": "부정청탁 및 금품등 수수의 금지에 관한 법률 시행령", "조문": "별표 1",
                  "약칭": ["청탁금지법 시행령"]}),
            tool({"결과": [{"법령": "부정청탁 및 금품등 수수의 금지에 관한 법률"}]})]
    real = "청탁금지법(부정청탁 및 금품등 수수의 금지에 관한 법률)에 따라 이 법은 부정청탁 및 금품등 수수의 금지에 관한 법률 시행령 별표 1을 따릅니다."
    assert loop.loose_unnamed_laws(real, msgs) == []
    fake = "청탁금지법(공직자청탁 및 금품등 수수 제한에 관한 법률)에 따르면"
    assert loop.loose_unnamed_laws(fake, msgs) == ["공직자청탁 및 금품등 수수 제한에 관한 법률"]
    assert loop.loose_unnamed_laws("농지법 시행령 제3조", msgs) == ["농지법 시행령"]


def test_unbracketed_names_written_by_the_user_count(monkeypatch):
    monkeypatch.setattr(settings, "answer_law_loose_pattern", LOOSE)
    monkeypatch.setattr(settings, "answer_law_keys", "법령")
    msgs = [{"role": "user", "content": "농지법 시행령에 농업인 기준이 있나요?"}]
    assert loop.loose_unnamed_laws("농지법 시행령 제3조를 보면", msgs) == []


def test_loose_law_name_check_is_off_without_a_pattern(monkeypatch):
    monkeypatch.setattr(settings, "answer_law_loose_pattern", "")
    assert loop.loose_unnamed_laws("공직자청탁 제한에 관한 법률", []) == []


def test_law_names_quoted_inside_looked_up_text_count(monkeypatch):
    # 실측 2026-10-29 P01: 조회한 농지법 제37조 원문에 「국토의 계획 및 이용에 관한 법률」이 있는데, 법령명 칸만 보아
    # 답·이야기에 쓴 그 이름을 확인 안 된 법령으로 걸었다 — 원문 안의 법령명(ANSWER_LAW_PATTERN)도 확인된 이름이다
    monkeypatch.setattr(settings, "answer_law_loose_pattern", LOOSE)
    monkeypatch.setattr(settings, "answer_law_pattern", r"「([^」\n]+)」")
    monkeypatch.setattr(settings, "answer_law_keys", "법령")
    msgs = [tool({"법령": "농지법", "원문": "다만, 「국토의 계획 및 이용에 관한 법률」에 따른 도시지역"})]
    assert loop.loose_unnamed_laws("국토의 계획 및 이용에 관한 법률에 따른 도시지역", msgs) == []
    assert loop.loose_unnamed_laws("건축물의 분양에 관한 법률", msgs) == ["건축물의 분양에 관한 법률"]
