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
