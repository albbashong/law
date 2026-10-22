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
