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


def test_near_verbatim_quotes_pass_when_a_match_ratio_is_set(monkeypatch):
    # ANSWER_QUOTE_MATCH_RATIO: 인용 글자 중 원문 한 곳과 차례대로 맞는 비율이 이 값 이상이면 원문 인용으로 본다(0 이면 그대로만).
    # 실측 2026-11-06: "…건축물 : 별표 20에 규정된 건축물"을 "…건축물은 별표 20에 규정된 건축물"로 옮긴 것이 확인 안 됨으로 붙었다
    monkeypatch.setattr(settings, "answer_quote_pattern", QUOTE)
    monkeypatch.setattr(settings, "answer_quote_min_chars", 6)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+")
    msgs = [article("국토의 계획 및 이용에 관한 법률 시행령", "제71조",
                    "19. 계획관리지역안에서 건축할 수 없는 건축물 : 별표 20에 규정된 건축물")]
    near = "제71조제1항제19호 “계획관리지역 안에서 건축할 수 없는 건축물은 별표 20에 규정된 건축물”"
    made = "제71조 “계획관리지역에서는 모든 공장을 지을 수 없다”"
    monkeypatch.setattr(settings, "answer_quote_match_ratio", 0.0)
    assert loop.misquoted(near, msgs) != []
    monkeypatch.setattr(settings, "answer_quote_match_ratio", 0.9)
    assert loop.misquoted(near, msgs) == []
    assert loop.misquoted(made, msgs) == ["계획관리지역에서는 모든 공장을 지을 수 없다"]


def test_a_quote_restating_the_question_is_not_a_law_quote(monkeypatch):
    # 실측 2026-11-09 G11: 질문을 조금 바꿔 따옴표로 다시 적은 줄에 조문 번호가 있어(질문에 든 번호) 확인 안 된 인용으로 붙었다
    monkeypatch.setattr(settings, "answer_quote_pattern", QUOTE)
    monkeypatch.setattr(settings, "answer_quote_min_chars", 6)
    monkeypatch.setattr(settings, "answer_quote_match_ratio", 0.9)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+")
    msgs = [{"role": "user", "content": "국토계획법 시행령 제84조 건폐율 규정이 영천시 조례에서는 어떻게 정해져 있어?"},
            article("영천시 도시계획 조례", "제56조", "법 제77조 및 영 제84조에 따른 건폐율은 다음 각 호와 같다.")]
    told = "**상황** – “국토계획법 시행령 제84조가 정하는 건폐율 규정이 영천시 조례에서는 어떻게 정해져 있는가”라는 질문입니다."
    made = "영 제84조 “자연녹지지역의 건폐율은 모두 60퍼센트로 한다”"
    assert loop.misquoted(told, msgs) == []
    assert loop.misquoted(made, msgs) == ["자연녹지지역의 건폐율은 모두 60퍼센트로 한다"]


def test_cites_only_listed_under_keys_the_tool_marked_as_pointers_are_not_grounded(monkeypatch):
    # 실측 2026-11-09 G23: building_basis 의 핵심_조문(열어 볼 조문 목록)에 이름만 있던 건축법 제11조 등을 원문을 열지 않고
    # 인용했는데, 이름이 도구 결과에 있어 확인된 인용으로 쳤다. 어느 칸이 목록인지는 도구가 결과에 표시한다(POINTER_KEY)
    monkeypatch.setattr(settings, "answer_cite_pattern", r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+")
    msgs = [tool({"용도": "축사", "핵심_조문": ["건축법 제11조(건축허가)", "건축법 제14조(건축신고)"]})]
    ans = "건축법 제11조에 따라 허가를 받습니다."
    assert loop.ungrounded(ans, msgs) == []
    assert loop.ungrounded(ans, msgs, pointers={"핵심_조문"}) == ["제11조"]
    opened = msgs + [article("건축법", "제11조", "건축물을 건축하려는 자는 허가를 받아야 한다.")]
    assert loop.ungrounded(ans, opened, pointers={"핵심_조문"}) == []


def test_law_names_joined_with_a_middle_dot_are_checked_one_by_one(monkeypatch):
    # 실측 2026-11-10 W3(영천시 공장): 표 첫 칸의 '법령·조례', '…법률·시행규칙', '…건축물은 영천시 도시계획 조례'를
    # 지어낸 법령명으로 잡아 답 끝에 확인 안 된 인용으로 붙였다
    monkeypatch.setattr(settings, "answer_law_pattern", LAW)
    monkeypatch.setattr(settings, "answer_law_keys", "법령,law")
    msgs = [article("가축분뇨의 관리 및 이용에 관한 법률", "제8조", "가축사육의 제한"),
            article("가축분뇨의 관리 및 이용에 관한 법률 시행규칙", "제5조", "거리"),
            article("영천시 도시계획 조례", "제33조", "공장")]
    rows = ["| 법령·조례 | 내용 |", "| 가축분뇨의 관리 및 이용에 관한 법률·시행규칙 | 거리 |",
            "| 해당 지역에서 건축할 수 있는 건축물은 영천시 도시계획 조례 | 공장 |"]
    for row in rows:
        assert loop.unnamed_laws(row, msgs) == [], row
    assert loop.unnamed_laws("| 영천시 공장 조례·가축 규칙 | 내용 |", msgs) == ["영천시 공장 조례·가축 규칙"]

