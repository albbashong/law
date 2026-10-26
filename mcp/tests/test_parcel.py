import json
from pathlib import Path

import httpx
import pytest

from law_mcp.clients import vworld
from law_mcp.tools.building_basis import run_building_basis
from law_mcp.tools.parcel import run_parcel_zoning

FIX = Path(__file__).parent / "fixtures"


def _transport(coord="vworld_coord.json", landchar="vworld_landchar.json"):
    def handler(req: httpx.Request):
        name = {"/req/address": coord, "/ned/data/getLandUseAttr": "vworld_landuse.json",
                "/ned/data/getLandCharacteristics": landchar}[req.url.path]
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_vworld():
    return vworld.Client("test-key", transport=_transport())


def test_lookup_yeongcheon(mock_vworld):
    p = mock_vworld.lookup("경상북도 영천시 금호읍 신월리 1-51")
    assert p.pnu == "4723025028100010051" and p.sigungu == "영천시" and p.sido == "경상북도"
    assert p.zones == ["자연녹지지역"] and p.land_category == "전" and p.area == "794"
    assert "대로1류(폭 35m~40m)(접함)" in p.others   # 접함·저촉 관계를 함께 적는다


def test_landchar_denied_is_optional():
    c = vworld.Client("k", transport=_transport(landchar="vworld_landchar_denied.json"), retry_wait=0)
    p = c.lookup("경상북도 영천시 금호읍 신월리 1-51")
    assert p.zones == ["자연녹지지역"] and p.land_category is None


def test_address_not_found():
    c = vworld.Client("k", transport=_transport(coord="vworld_coord_notfound.json"))
    r = run_parcel_zoning("없는주소 999", lookup=c.lookup)
    assert r["status"] == "failed" and "예:" in r["message"]


def test_unavailable():
    def boom(req):
        raise httpx.ConnectError("down")
    c = vworld.Client("k", transport=httpx.MockTransport(boom), retry_wait=0)
    r = run_parcel_zoning("경상북도 영천시 금호읍 신월리 1-51", lookup=c.lookup)
    assert r["status"] == "failed" and "브이월드" in r["message"]


def test_building_basis_with_address(store, mock_vworld):
    r = run_building_basis(store, "단독주택", address="경상북도 영천시 금호읍 신월리 1-51",
                           parcel_lookup=mock_vworld.lookup)
    f = r["facts"]
    assert f["용도지역"] == "자연녹지지역" and f["근거상태"] == "시행령_허용"
    assert f["필지"]["pnu"] == "4723025028100010051"
    assert any(x["law"] == "농지법" for x in f["refs"])


def _road_transport():
    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/req/address":
            name = "vworld_coord_road_parcel.json" if req.url.params["type"] == "PARCEL" else "vworld_coord_road.json"
        else:
            name = {"/req/search": "vworld_search_road.json", "/ned/data/getLandUseAttr": "vworld_landuse_gs.json",
                    "/ned/data/getLandCharacteristics": "vworld_landchar_denied.json"}[p]
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))
    return httpx.MockTransport(handler)


def test_road_address_resolves_pnu_and_multi_zone():
    p = vworld.Client("k", transport=_road_transport()).lookup("경상북도 경산시 남매로 159")
    assert p.pnu == "4729011700107010017" and p.sigungu == "경산시"
    assert p.zones == ["제2종일반주거지역", "일반상업지역"]


def test_transient_incorrect_key_is_retried():
    calls = {"n": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/ned/data/getLandUseAttr":
            calls["n"] += 1
            if calls["n"] == 1:   # 브이월드는 가끔 정상 키에도 INCORRECT_KEY 를 낸다
                return httpx.Response(200, json={"landUses": {"resultCode": "INCORRECT_KEY",
                                                              "resultMsg": "인증키 정보가 올바르지 않습니다."}})
        name = {"/req/address": "vworld_coord.json", "/ned/data/getLandUseAttr": "vworld_landuse.json",
                "/ned/data/getLandCharacteristics": "vworld_landchar.json"}[p]
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))

    c = vworld.Client("k", transport=httpx.MockTransport(handler), retry_wait=0)
    p = c.lookup("경상북도 영천시 금호읍 신월리 1-51")
    assert p.zones == ["자연녹지지역"] and calls["n"] == 2


def test_parcel_zoning_adds_conversion_permit_refs(store, mock_vworld):
    r = run_parcel_zoning("경상북도 영천시 금호읍 신월리 1-51", lookup=mock_vworld.lookup, store=store)
    f = r["facts"]
    assert f["지목"] == "전"
    assert any(x["law"] == "농지법" and x["article"].startswith("제34조") for x in f["refs"])


def test_error_burst_up_to_four_times_is_retried():
    calls = {"n": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/ned/data/getLandUseAttr":
            calls["n"] += 1
            if calls["n"] <= 4:   # 오류가 몰려 오는 경우
                return httpx.Response(200, json={"landUses": {"resultCode": "INCORRECT_KEY"}})
        name = {"/req/address": "vworld_coord.json", "/ned/data/getLandUseAttr": "vworld_landuse.json",
                "/ned/data/getLandCharacteristics": "vworld_landchar.json"}[p]
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))

    c = vworld.Client("k", transport=httpx.MockTransport(handler), retry_wait=0)
    assert c.lookup("경상북도 영천시 금호읍 신월리 1-51").zones == ["자연녹지지역"]


def test_unavailable_message_names_endpoint():
    def handler(req):
        return httpx.Response(200, json={"response": {"status": "ERROR", "error": {"code": "INCORRECT_KEY"}}})
    c = vworld.Client("k", transport=httpx.MockTransport(handler), retry_wait=0)
    with pytest.raises(vworld.VworldUnavailable, match="/req/address"):
        c.lookup("x")


def test_lookup_reads_road_side_and_legal_dong(mock_vworld):
    """대지와 도로의 관계(건축법 제44조)와 그 적용 지역(제3조제2항: 읍·면·동)을 판단할 사실."""
    p = mock_vworld.lookup("경상북도 영천시 금호읍 신월리 1-51")
    assert p.road_side == "세로한면(가)" and p.legal_dong == "경상북도 영천시 금호읍 신월리"
    r = run_parcel_zoning("경상북도 영천시 금호읍 신월리 1-51", lookup=mock_vworld.lookup)
    assert r["facts"]["도로접면"] == "세로한면(가)" and r["facts"]["법정동"] == "경상북도 영천시 금호읍 신월리"


def test_road_side_missing_is_left_out():
    c = vworld.Client("k", transport=_transport(landchar="vworld_landchar_denied.json"), retry_wait=0)
    r = run_parcel_zoning("경상북도 영천시 금호읍 신월리 1-51", lookup=c.lookup)
    assert "도로접면" not in r["facts"]


def _picky_transport(known: str):
    """`known` 주소로만 좌표가 나오는 브이월드 — 잘못 붙인 시도·읍면을 빼고 다시 찾는지 본다."""
    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/req/address":
            name = "vworld_coord.json" if req.url.params["address"] == known else "vworld_coord_notfound.json"
        else:
            name = {"/ned/data/getLandUseAttr": "vworld_landuse.json",
                    "/ned/data/getLandCharacteristics": "vworld_landchar.json"}[p]
        return httpx.Response(200, json=json.loads((FIX / name).read_text()))
    return httpx.MockTransport(handler)


def test_lookup_drops_a_wrong_sido_and_says_so():
    c = vworld.Client("k", transport=_picky_transport("영천시 금호읍 신월리 1-51"))
    p = c.lookup("경상남도 영천시 금호읍 신월리 1-51")
    assert p.pnu == "4723025028100010051" and p.sido == "경상북도"
    assert any("경상남도 영천시 금호읍 신월리 1-51" in n for n in p.notes)


def test_lookup_drops_a_wrong_eup_myeon():
    c = vworld.Client("k", transport=_picky_transport("영천시 신월리 1-51"))
    assert c.lookup("영천시 금호면 신월리 1-51").pnu == "4723025028100010051"


def test_lookup_rejects_a_fallback_that_lands_elsewhere():
    # 시군구를 빼고 찾지 않는다 — 같은 리 이름이 다른 시군구에도 있다
    c = vworld.Client("k", transport=_picky_transport("금호읍 신월리 1-51"))
    with pytest.raises(vworld.AddressNotFound):
        c.lookup("경산시 금호읍 신월리 1-51")


def _fake_lookup(zones):
    def lookup(address):
        return vworld.Parcel("경상북도 영천시 금호읍 신월리 1-51", "4723025028100010051", "경상북도", "영천시",
                             zones=list(zones), land_category="전", area="794")
    return lookup


def test_only_real_use_zones_split_the_answer(store):
    # 브이월드 '…지역' 목록에는 도시지역(대분류)·규제지역도 섞여 온다 — 용도지역으로 해석되는 것만 쓴다
    r = run_building_basis(store, "단독주택", address="x",
                           parcel_lookup=_fake_lookup(["도시지역", "자연녹지지역", "(낙동강)건축허가제한지역"]))
    assert r["status"] == "ok", r["message"]
    assert r["facts"]["용도지역"] == "자연녹지지역" and "용도지역별" not in r["facts"]


def test_unknown_use_on_a_multi_zone_parcel_still_returns_the_use_list(store):
    r = run_building_basis(store, "노래방", address="x",
                           parcel_lookup=_fake_lookup(["자연녹지지역", "생산녹지지역"]))
    assert r["status"] == "failed" and r["facts"]["용도목록"]


def _lookup_with_others(others):
    def lookup(address):
        return vworld.Parcel("경상북도 영천시 금호읍 신월리 1-51", "4723025028100010051", "경상북도", "영천시",
                             zones=["자연녹지지역"], others=list(others), land_category="전", area="794")
    return lookup


def test_parcel_regulation_gets_the_article_that_designates_it(store):
    # 기타규제 이름이 조문 제목에 그대로 나오는 법령 조문을 붙이고, 그런 규제는 답에서 빠지지 않게 런타임 키로도 둔다
    from law_mcp.tools.building_basis import run_zone_ratios
    r = run_zone_ratios(store, address="x", parcel_lookup=_lookup_with_others(["성장관리계획구역", "소로2류(폭 8m~10m)(접합)"]))
    rows = {x["규제"]: x for x in r["facts"]["필지"]["규제_근거"]}
    assert any("제75조의2" in b for b in rows["성장관리계획구역"]["근거"])
    assert "근거" not in rows["소로2류(폭 8m~10m)(접합)"]
    assert r["mention"] == ["성장관리계획구역"]


def test_regulation_named_by_a_common_title_word_gets_no_basis(store, monkeypatch):
    from law_mcp.engine.regulations import regulation_basis
    monkeypatch.setenv("REGULATION_TITLE_DF_MAX", "1")
    assert regulation_basis(store, "개발행위(저촉)") == []


def test_parcel_without_regulations_has_no_mention(store):
    r = run_parcel_zoning("x", lookup=_lookup_with_others([]), store=store)
    assert "mention" not in r and "규제_근거" not in r["facts"]


def test_regulation_absent_from_titles_gets_the_article_defining_it():
    # 제목에 없는 이름은 "N. 이름: …" 처럼 정의하는 조문으로 찾는다(산지관리법 제4조의 준보전산지)
    from law_mcp.engine.regulations import _defines
    assert _defines("① 산지를 다음과 같이 구분한다.\n2. 준보전산지: 보전산지 외의 산지", "준보전산지")
    assert not _defines("준보전산지에 지구단위계획구역을 지정할 수 있다", "준보전산지")
