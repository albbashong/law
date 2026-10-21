import json

import pytest

from indexer.build import build
from law_mcp.store.db import Store
from law_mcp.tools.building_basis import run_building_basis


def test_yeongcheon_factory(store):
    r = run_building_basis(store, "공장", region="영천시", zone="자연녹지지역", land_category="전")
    f = r["facts"]
    assert r["status"] == "ok", r["message"]
    # 조례 별표 16 제15호는 공장 중 열거된 업종만 허용 — 일부만 허용하는 것으로 본다
    assert f["근거상태"] == "조건부_허용"
    assert any("제15호" in c for c in f["조건"])
    arts = {(x["law"], x["article"]) for x in f["refs"]}
    assert ("국토의 계획 및 이용에 관한 법률 시행령", "별표 17 제2호 차목") in arts
    assert any(l == "영천시 도시계획 조례" and a.startswith("별표 16 제15호") for l, a in arts)
    assert any(l == "국토의 계획 및 이용에 관한 법률" and a.startswith("제76조") for l, a in arts)
    assert "20" in f["건폐율"] and "100" in f["용적률"]
    assert any(x["law"] == "농지법" for x in f["refs"])
    assert len(json.dumps(f, ensure_ascii=False)) <= 7600


def test_house_allowed_directly(store):
    r = run_building_basis(store, "단독주택", region="영천시", zone="자연녹지지역")
    assert r["facts"]["근거상태"] == "시행령_허용"


def test_not_listed(store):
    r = run_building_basis(store, "위락시설", region="영천시", zone="자연녹지지역")
    assert r["facts"]["근거상태"] == "미열거"


@pytest.fixture(scope="module")
def store_without_ordin_annex(tmp_path_factory, monkeypatch_module):
    import indexer.build as b
    real = b._annex_rows

    def no_ordin(source, msts):
        return {} if source == "ordin" else real(source, msts)

    monkeypatch_module.setattr(b, "_annex_rows", no_ordin)
    out = tmp_path_factory.mktemp("noannex") / "db.sqlite"
    b.build(out, docs=[("law", "284013"), ("law", "288857"), ("law", "290833"), ("ordin", "2000711")],
            log=lambda m: None)
    return Store(out)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_missing_ordinance_annex_partial(store_without_ordin_annex):
    r = run_building_basis(store_without_ordin_annex, "공장", region="영천시", zone="자연녹지지역")
    assert r["status"] == "partial" and r["facts"]["근거상태"] == "미확인"


def test_ambiguous_region(store):
    r = run_building_basis(store, "공장", region="중구", zone="자연녹지지역")
    assert r["status"] == "needs_disambiguation"
    assert len(r["facts"]["candidates"]) >= 2


def test_unknown_use(store):
    r = run_building_basis(store, "우주정거장", region="영천시", zone="자연녹지지역")
    assert r["status"] == "failed" and "용도" in r["message"]


def test_unknown_zone(store):
    r = run_building_basis(store, "공장", region="영천시", zone="달나라지역")
    assert r["status"] == "failed" and "용도지역" in r["message"]


def test_no_definitive_verdict_wording(store):
    r = run_building_basis(store, "공장", region="영천시", zone="자연녹지지역")
    assert "가능합니다" not in r["message"] and "불가합니다" not in r["message"]


def test_conditional_prohibition_planning_area(store):
    r = run_building_basis(store, "공장", region="영천시", zone="계획관리지역")
    f = r["facts"]
    assert f["근거상태"] == "조건부_제한"
    assert any("성장관리계획" in c for c in f["조건"])


def test_outright_prohibition_planning_area(store):
    r = run_building_basis(store, "업무시설", region="영천시", zone="계획관리지역")
    assert r["facts"]["근거상태"] == "미열거"


def test_not_prohibited_in_cannot_type_annex(store):
    r = run_building_basis(store, "단독주택", region="영천시", zone="계획관리지역")
    f = r["facts"]
    assert f["근거상태"] == "시행령_허용"
    assert "금지 목록에 없음" in f["refs"][0]["note"]


def test_ordinance_with_article_based_restrictions(store):
    # 수원시는 용도지역 건축제한을 별표가 아니라 제46조(자연녹지지역 안에서의 건축제한) 각 호에 둔다
    r = run_building_basis(store, "공장", region="수원시", zone="자연녹지지역")
    f = r["facts"]
    assert r["status"] == "ok" and f["근거상태"] == "조건부_허용"   # 첨단업종·식품공장 등만
    assert any(x["law"] == "수원시 도시계획 조례" and x["article"].startswith("제46조제9호") for x in f["refs"])


# --- 최종 리뷰 C1: 한 용도가 시행령 별표 여러 구분에 걸치는 경우 ---

def test_use_split_between_allowed_and_ordinance(store):
    # 자연녹지 제2종근생: 제1호 다목(자목 등 제외, 바로 허용) + 제2호 나목(자목 등, 조례로) → 조례 확인
    r = run_building_basis(store, "일반음식점", region="영천시", zone="자연녹지지역")
    f = r["facts"]
    assert f["근거상태"] == "조례_허용", f
    assert any(x["law"] == "영천시 도시계획 조례" and x["article"].startswith("별표 16 제4호") for x in f["refs"])


def test_subtype_ban_by_mok_is_partial(store):
    # 계획관리 제2종근생: 금지는 일부 유형(라목 (1)~(3), 더목 단란주점 등)뿐 → 조건부_제한, 조례 별표도 확인
    r = run_building_basis(store, "제2종 근린생활시설", region="영천시", zone="계획관리지역")
    f = r["facts"]
    assert f["근거상태"] == "조건부_제한", f
    assert any(x["law"] == "영천시 도시계획 조례" for x in f["refs"])


def test_partial_ban_plus_ordinance_restriction_consults_ordinance(store):
    # 준주거 판매시설: 제1호 나목(일반게임제공업만 금지) + 제2호 다목(조례로 금지 가능) → 조례는 일반게임제공업만 금지
    r = run_building_basis(store, "판매시설", region="영천시", zone="준주거지역")
    f = r["facts"]
    assert f["근거상태"] == "조건부_제한", f
    assert any(x["law"] == "영천시 도시계획 조례" and x["article"].startswith("별표 6 제2호") for x in f["refs"])


def test_address_keeps_sido_for_same_name_district(store):
    from law_mcp.clients.vworld import Parcel
    p = Parcel("서울특별시 중구 어딘가 1", "1111010100100010000", "서울특별시", "중구", ["자연녹지지역"])
    r = run_building_basis(store, "단독주택", address="서울 중구 어딘가 1", parcel_lookup=lambda a: p)
    assert r["status"] == "ok" and r["facts"]["시군구"].startswith("서울특별시"), r["message"]


def test_multi_zone_aggregates_worst_status(store):
    from law_mcp.clients.vworld import Parcel
    p = Parcel("영천시 어딘가", "4723000000100010000", "경상북도", "영천시", ["자연녹지지역", "없는용도지역"])
    r = run_building_basis(store, "공장", address="영천시 어딘가", parcel_lookup=lambda a: p)
    assert r["status"] == "partial"
    zones = {z["용도지역"]: z for z in r["facts"]["용도지역별"]}
    assert zones["자연녹지지역"]["근거상태"] == "조건부_허용"
    assert "없는용도지역" in str(r["facts"]["용도지역별"])


def test_related_permits_come_from_data(store):
    r = run_building_basis(store, "공장", region="영천시", zone="자연녹지지역", land_category="전")
    extra = " ".join(r["facts"]["추가검토"])
    assert "농지법 제34조" in extra and "산업집적활성화 및 공장설립에 관한 법률 제13조" in extra
    assert "제56조" in extra
    assert r["facts"]["근거상태_설명"]


def test_unknown_use_returns_annex_1_names(store):
    r = run_building_basis(store, "카페", region="영천시", zone="자연녹지지역")
    assert r["status"] == "failed"
    assert any("제1종 근린생활시설" in n for n in r["facts"]["용도목록"])


def test_missing_land_category_hint(store):
    # 지목 없이 부르면, 질문에 토지 종류가 있었을 경우 land_category 로 다시 부르라고 알린다
    r = run_building_basis(store, "단독주택", region="영천시", zone="자연녹지지역")
    assert "land_category" in r["message"]
    r = run_building_basis(store, "단독주택", region="영천시", zone="자연녹지지역", land_category="전")
    assert "land_category" not in r["message"]
