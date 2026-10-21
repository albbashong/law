"""별표 정리 — 여러 별표를 파일 하나로 올린 합본은 본문의 '[별표 N]' 머리말로 쪼개고, 번호·제목은 본문
머리말에서 읽는다(목록 API 의 번호가 밀려 있거나 제목이 '[별표 17]' 뿐인 곳)."""
from indexer.annex_split import normalize_annexes

MERGED = """【별표 1】 <개정 2012. 11. 5.>
제1종전용주거지역안에서 건축할 수 있는 건축물(제29조제1호 관련)
1. 건축법 시행령
별표 1 제1호의 단독주택중 다가구주택을 제외한 것
【별표 2】
제2종전용주거지역안에서 건축할 수 있는 건축물
1. 내용
【별표 16】〈개정 2014.5.13.〉 자연녹지지역안에서 건축할 수 있는 건축물 (제29조제16호 관련)
1. 영 별표 17 제1호의 건축물
"""


def test_merged_annex_is_split_at_its_bracketed_headers():
    out = normalize_annexes([("0001", "00", "별표 1 ~ 별표 25", MERGED)])
    assert [(no, title) for no, title, _ in out] == [
        ("1", "제1종전용주거지역안에서 건축할 수 있는 건축물(제29조제1호 관련)"),
        ("2", "제2종전용주거지역안에서 건축할 수 있는 건축물"),
        ("16", "자연녹지지역안에서 건축할 수 있는 건축물 (제29조제16호 관련)"),
    ]
    assert out[0][2].startswith("【별표 1】") and "별표 1 제1호의 단독주택" in out[0][2]   # 본문 속 인용은 머리말이 아니다
    assert out[2][2].rstrip().endswith("영 별표 17 제1호의 건축물")


def test_header_number_and_title_replace_shifted_listing():
    text = "[별표 17]\n자연녹지지역 안에서 건축할 수 있는 건축물\n(제37조제16호 관련)\n1. 내용"
    assert normalize_annexes([("0018", "00", "[별표 17]", text)]) == [
        ("17", "자연녹지지역 안에서 건축할 수 있는 건축물", text)]


def test_numbers_going_backwards_are_not_new_annexes():
    text = "[별표 1] 가\n[별표 1] 다시 언급\n[별표 2] 나\n내용"
    out = normalize_annexes([("0001", "00", "합본", text)])
    assert [no for no, _, _ in out] == ["1", "2"]


def test_branch_numbers_are_kept():
    text = "[별표 3] 셋\n내용\n[별표 3의2] 셋의 둘\n내용"
    assert [no for no, _, _ in normalize_annexes([("0003", "00", "x", text)])] == ["3", "3의2"]


def test_annex_without_headers_or_text_is_left_as_listed():
    assert normalize_annexes([("0002", "01", "정해진 제목", "머리말 없는 본문"), ("0004", "00", "빈 것", None)]) == [
        ("2의1", "정해진 제목", "머리말 없는 본문"), ("4", "빈 것", None)]


def test_headers_after_the_ordinance_name_mark_are_split():
    text = "[별표 1]\n개발행위허가기준\n내용\n■ 강릉시 도시계획 조례 [별표 2] <개정 2015.6.24.>\n제1종전용주거지역 안에서 건축할 수 있는 건축물\n내용"
    out = normalize_annexes([("0001", "00", "별표 1~별표 26", text)])
    assert [(no, title) for no, title, _ in out] == [("1", "개발행위허가기준"),
                                                     ("2", "제1종전용주거지역 안에서 건축할 수 있는 건축물")]


def test_revision_notes_and_the_ordinance_name_are_not_titles():
    text = ("[별표 1]\n(개정 2006. 10. 4, 2010. 1. 1)\n가평군 군계획 조례\n제1종일반주거지역 안에서 건축할 수 있는 건축물\n"
            "[별표 2] (종전 별표 1의4에서 이동 2024. 1. 1)\n준주거지역 안에서 건축할 수 없는 건축물")
    out = normalize_annexes([("0001", "00", "합본", text)], doc_name="가평군 군계획 조례")
    assert [title for _, title, _ in out] == ["제1종일반주거지역 안에서 건축할 수 있는 건축물",
                                              "준주거지역 안에서 건축할 수 없는 건축물"]


def test_descriptive_listing_title_is_kept_for_a_single_annex():
    text = "[별표 15]〈개정 2018. 12. 13〉\n양평군 도시계획 조례\n생산녹지지역 안에서 건축할 수 있는 건축물"
    assert normalize_annexes([("0015", "00", "[별표 15] 생산녹지지역 안에서 건축할 수 있는 건축물", text)]) == [
        ("15", "[별표 15] 생산녹지지역 안에서 건축할 수 있는 건축물", text)]
