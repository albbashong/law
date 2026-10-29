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
    assert any(x["조문"].startswith("영천시 도시계획 조례 제26조") for x in f["토지분할_허가기준"])


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
    # 실측 2026-10-28·29 W7: "나. 분할제한면적 미만으로의 토지의 분할"을 허가가 필요 없다고 거꾸로 읽었다 —
    # 항목마다 그 항목이 딸린 목록의 첫머리(무엇의 목록인지)를 위에서부터 칸으로 따로 준다
    items = run_split_area(store, region="영천시", zone="계획관리지역")["facts"]["토지분할_허가기준"]
    under = [x for x in items if "분할제한면적 미만" in x["해당"]]
    assert under and all("개발행위허가를 받아야" in x["딸린_문장"][0] and "토지분할" in x["딸린_문장"][-1] for x in under), under


def test_list_lead_in_the_article_body_is_kept(store):
    # 제53조처럼 항 없이 조 본문이 "…경미한 행위란 다음 각 호의 행위를 말한다"로 여는 목록 — 조 첫머리를 놓치면
    # 그 아래 목이 허가 없이 되는 것이라는 사실이 빠진다
    items = run_split_area(store, region="영천시", zone="계획관리지역")["facts"]["토지분할_허가기준"]
    minor = [x for x in items if "이상으로의 분할" in x["해당"]]
    assert minor and all("경미한 행위" in x["딸린_문장"][0] for x in minor), minor


def test_amendment_marks_are_dropped_from_the_lead(store):
    items = run_split_area(store, region="영천시", zone="계획관리지역")["facts"]["토지분할_허가기준"]
    assert not any("<개정" in line for x in items for line in x["딸린_문장"])


def test_message_asks_for_the_land_category_when_not_given(store):
    # 지목 없이 부르면 지목 법률의 분할 제한은 없다 — land_category 로 다시 부르라고 알린다(W7 에서 농지법을 잃었다)
    assert "land_category" in run_split_area(store, region="영천시", zone="계획관리지역")["message"]


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


def test_land_category_brings_the_split_ban_of_the_law_defining_it(store):
    # 실측 2026-10-28 W7 "경지정리된 농지도 분할할 수 있어?": 분할 도구가 지목 법률은 따로 찾으라고만 해 농지법 제22조를
    # 놓쳤다 — 지목(답)·법령 용어(농지)를 받으면 그 땅을 정의하는 법률에서 분할을 금지하는 항을 찾아 준다
    for land in ("답", "농지"):
        f = run_split_area(store, land_category=land)["facts"]
        assert any(x.startswith("농지법 제22조제2항") for x in f["지목_분할제한"]), (land, f.get("지목_분할제한"))
        assert any("2천제곱미터" in x.replace(" ", "") or "2,000" in x for x in f["지목_분할제한"])


def test_parcel_land_category_is_used(store):
    from law_mcp.clients.vworld import Parcel
    p = Parcel("경상북도 영천시 어딘가 1", "4723000000100010000", "경상북도", "영천시", ["자연녹지지역"], land_category="답")
    f = run_split_area(store, address="x", parcel_lookup=lambda a: p)["facts"]
    assert any(x.startswith("농지법 제22조") for x in f["지목_분할제한"])
