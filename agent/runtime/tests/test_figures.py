"""수치 뽑기 — 답·원문에서 단위별(비율·금액·면적·길이·기간·층·대수) 수치를 값으로 뽑아 표기가 달라도 비교한다."""
import figures


def claims(text):
    return {(f, v) for _, f, v in figures.claims(text)}


def test_money_in_korean_units():
    assert claims("5만원, 50,000원, 1억 5천만원, 100만 원") == {("원", 50000), ("원", 150000000), ("원", 1000000)}


def test_area_length_floors_counts():
    assert claims("500㎡ 이상, 85제곱미터, 1,000m 이내, 4층 이하, 134㎡당 1대") == {
        ("㎡", 500), ("㎡", 85), ("m", 1000), ("층", 4), ("㎡", 134), ("대", 1)}


def test_durations_skip_dates():
    assert claims("계속근로 1년 이상, 2년, 30일분, 2024년 8월 27일 개정") == {("년", 1), ("년", 2), ("일", 30)}


def test_rates_come_along():
    assert ("비율", 0.0025) in claims("과세표준의 1천분의 2.5")


def test_unmatched_compares_values_within_a_unit():
    source = "숙박비 상한액: 서울특별시 100,000, 식비 25,000원, 계속근로기간 1년에 대하여 30일분"
    assert figures.unmatched("서울은 10만원, 식비 2만5천원, 1년 이상 근무하면 30일분", source) == []
    assert figures.unmatched("식비는 3만원이고 2년 이상", source) == ["3만원", "2년"]


def test_thin_space_thousands_separator():
    """모델이 천 단위를 좁은 공백(U+202F)으로 끊어 쓰는 일이 있다 — '100 000원'도 100,000원이다."""
    assert claims("상한액은 100 000원") == {("원", 100000)}


def test_korean_multipliers_on_areas():
    assert claims("1만 제곱미터 미만, 5천㎡, 3만㎡") == {("㎡", 10000), ("㎡", 5000), ("㎡", 30000)}
    assert figures.unmatched("10,000㎡ 미만", "자연녹지지역 : 1만 제곱미터 미만") == []
