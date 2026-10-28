"""분할 제한면적 — 건축법의 분할 제한 조문에서 시행령·조례로 위임을 따라 내려가 용도지역의 면적을 찾는다.
실측 2026-10-28: 이 경로가 없어 검색에 걸린 다른 법령의 330㎡(농지법 시행령)를 최소분할면적으로 답했다."""
import json

from law_mcp.tools.split_area import run_split_area


def _rows(r):
    return {z["용도지역"]: z for z in r["facts"]["용도지역별"]}


def test_ordinance_value_for_the_zone_class(store):
    r = run_split_area(store, region="영천시", zone="제1종일반주거지역")
    assert r["status"] == "ok", r["message"]
    row = _rows(r)["제1종일반주거지역"]
    assert row["분할제한면적"].startswith("60")
    assert row["조례"].startswith("영천시 건축 조례 제35조제1호")
    assert row["시행령"].startswith("건축법 시행령 제80조제1호")
    arts = {(x["law"], x["article"]) for x in r["facts"]["refs"]}
    assert ("건축법", "제57조") in arts or any(l == "건축법" and a.startswith("제57조") for l, a in arts)
    assert len(json.dumps(r["facts"], ensure_ascii=False)) <= 7600


def test_zone_outside_named_classes_takes_the_remaining_item(store):
    # 계획관리지역은 주거·상업·공업·녹지 어느 호에도 이름이 없다 — "해당하지 아니하는 지역" 호
    row = _rows(run_split_area(store, region="영천시", zone="계획관리지역"))["계획관리지역"]
    assert "해당하지 아니하는" in row["구분"] and row["분할제한면적"].startswith("60")


def test_green_zone(store):
    row = _rows(run_split_area(store, region="영천시", zone="자연녹지지역"))["자연녹지지역"]
    assert row["구분"].startswith("녹지지역") and row["분할제한면적"].startswith("200")


def test_land_split_permit_criteria_from_the_plan_ordinance(store):
    # 건축물이 없는 토지는 개발행위허가(토지분할) 기준 — 도시계획 조례가 건축법의 분할제한면적을 끌어 쓴다
    f = run_split_area(store, region="영천시", zone="계획관리지역")["facts"]
    assert any(x.startswith("영천시 도시계획 조례 제26조") for x in f["토지분할_허가기준"])


def test_without_region_gives_the_decree_ranges(store):
    r = run_split_area(store, zone="자연녹지지역")
    row = _rows(r)["자연녹지지역"]
    assert row["분할제한면적"].startswith("200") and "조례" in row["분할제한면적"]
    assert "region" in r["message"]


def test_without_zone_lists_every_class(store):
    r = run_split_area(store, region="영천시")
    assert {z["구분"].split(":")[0] for z in r["facts"]["용도지역별"]} >= {"주거지역", "녹지지역"}


def test_address_uses_only_zones_and_keeps_parcel(store):
    from law_mcp.clients.vworld import Parcel
    p = Parcel("경상북도 영천시 어딘가 1", "4723000000100010000", "경상북도", "영천시",
               ["공장설립승인지역", "도시지역", "자연녹지지역"], land_category="답", area="23")
    r = run_split_area(store, address="영천시 어딘가 1", parcel_lookup=lambda a: p)
    assert set(_rows(r)) == {"자연녹지지역"}            # 규제지역·대분류는 용도지역으로 보지 않는다
    assert r["facts"]["필지"]["지목"] == "답"


def test_criteria_keep_the_heading_they_belong_to(store):
    # 실측 2026-10-28 W7: "나. 분할제한면적 미만으로의 토지의 분할"만 보고 허가가 필요 없다고 거꾸로 읽었다 —
    # 그 목이 어느 조(개발행위허가의 대상)·호(토지분할)에 딸렸는지 함께 준다
    lines = run_split_area(store, region="영천시", zone="계획관리지역")["facts"]["토지분할_허가기준"]
    under = [x for x in lines if "분할제한면적 미만" in x]
    assert under and all("개발행위" in x and "토지분할" in x.split("나.")[0] for x in under), under


def test_message_says_land_category_laws_are_not_covered(store):
    # 농지·산지 등 지목을 정하는 법률의 분할 제한은 이 결과에 없다 — 따로 찾으라고 알린다(W7 에서 농지법을 잃었다)
    assert "law_search" in run_split_area(store, region="영천시", zone="계획관리지역")["message"]


def test_message_asks_to_split_the_answer_by_building_presence(store):
    # 실측 W3·W6: 지목 '답' 필지를 "건축물이 있는 대지이므로"라고 단정했다 — 건축물 유무는 이 결과로 모른다
    msg = run_split_area(store, region="영천시", zone="계획관리지역")["message"]
    assert "건축물" in msg and "나눠" in msg


def test_zone_lines_pick_the_item_naming_the_zone_class():
    # 실측 W3: 조례 조문 첫머리만 잘라 보여 녹지지역 200㎡ 호가 계획관리지역에 붙었다 — 그 용도지역이 든 호를 고른다
    from law_mcp.tools.split_area import _zone_lines
    units = [{"path": "제22조/1", "label": "제22조제1항", "text": "① 다음 각 호의 경우 토지분할을 제한할 수 있다."},
             {"path": "제22조/1/1", "label": "제22조제1항제1호", "text": "1. 녹지지역으로서 면적이 200제곱미터 미만인 경우"},
             {"path": "제22조/1/2", "label": "제22조제1항제2호",
              "text": "2. 관리지역ㆍ농림지역ㆍ자연환경보전지역으로서 면적이 60제곱미터 미만인 경우"},
             {"path": "제22조/1/3", "label": "제22조제1항제3호", "text": "3. 택지식 분할인 경우"}]
    assert [u["label"] for u in _zone_lines(units, "계획관리지역")] == ["제22조제1항제2호"]
    assert [u["label"] for u in _zone_lines(units, "자연녹지지역")] == ["제22조제1항제1호"]
