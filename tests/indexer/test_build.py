import sqlite3

import pytest

from indexer.build import build

SUBSET = [
    ("law", "284013"),    # 국토의 계획 및 이용에 관한 법률
    ("law", "288857"),    # 같은 법 시행령
    ("law", "273437"),    # 건축법
    ("law", "290833"),    # 건축법 시행령
    ("law", "284301"),    # 농지법
    ("ordin", "2000711"),  # 영천시 도시계획 조례
    ("ordin", "2149501"),  # 서울특별시 도시계획 조례
    ("ordin", "2120465"),  # 부산광역시 도시계획 조례
    ("ordin", "2143463"),  # 강원특별자치도 도시계획 조례
    ("ordin", "2160495"),  # 춘천시 도시계획 조례
    ("ordin", "1592205"),  # 서울특별시 강남구 도시계획 조례 (위임 사항만 — 건축제한은 서울시 조례)
    ("ordin", "2164393"),  # 무안군 관리계획 조례 — 이름이 '도시·군계획 조례' 꼴이 아닌 계획 조례
    ("ordin", "2084475"),  # 광주광역시 도시계획 조례 — 지자체 이름은 '(구)광주광역시', 자치구는 '전남광주통합특별시 ○구'
    ("ordin", "2152729"),  # 전남광주통합특별시 북구 조례 — 광주광역시 도시계획 조례를 인용한다
    ("ordin", "2141709"),  # 전남광주통합특별시 남구 조례 — 계획 조례를 인용하지 않는다
    ("ordin", "2096577"),  # 제주특별자치도 도시계획 조례
]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("db") / "t.sqlite"
    report = build(out, docs=SUBSET)
    return sqlite3.connect(out), report


def _doc(db, name):
    return db.execute("select doc_id from doc where name=?", (name,)).fetchone()[0]


def test_zone_map(built):
    db, _ = built
    assert db.execute("select annex_no from zone_map where zone='자연녹지지역'").fetchone() == ("17",)


def test_region_plan_variants(built):
    db, _ = built
    did = _doc(db, "영천시 도시계획 조례")
    for key in ("영천시", "영천", "경상북도 영천시"):
        assert db.execute("select doc_id from region_plan where region=?", (key,)).fetchone()[0] == did


def test_region_plan_metro_district_ambiguous(built):
    db, _ = built
    rows = db.execute("select doc_id from region_plan where region='중구'").fetchall()
    assert len({r[0] for r in rows}) >= 2


def test_ordinance_relative_citation_resolves(built):
    db, _ = built
    did = _doc(db, "영천시 도시계획 조례")
    rows = db.execute("""select d.name, l.to_path from link l join doc d on d.doc_id=l.to_doc
                         where l.from_doc=? and l.from_path like '제56조%' and l.kind='인용'""",
                      (did,)).fetchall()
    assert ("국토의 계획 및 이용에 관한 법률 시행령", "제84조/1") in rows


def test_enforcement_cites_parent_law(built):
    db, _ = built
    sid = _doc(db, "국토의 계획 및 이용에 관한 법률 시행령")
    law = _doc(db, "국토의 계획 및 이용에 관한 법률")
    n = db.execute("select count(*) from link where from_doc=? and to_doc=? and kind='인용'",
                   (sid, law)).fetchone()[0]
    assert n > 50


def test_hierarchy_from_the_official_tree(built):
    db, _ = built
    sid = _doc(db, "건축법 시행령")
    law = _doc(db, "건축법")
    assert db.execute("select evidence from link where from_doc=? and to_doc=? and kind='체계'",
                      (sid, law)).fetchone() == ("법령체계도",)


def test_law_missing_from_the_tree_takes_its_parent_from_its_purpose_article(tmp_path):
    # 체계도에 없는 하위 법령은 제1조(목적)의 "「○○법」에서 위임된 사항"으로 상위법을 찾는다 — 이름 꼴('… 시행령')은 보지 않는다
    empty = tmp_path / "links"
    empty.mkdir()
    out = tmp_path / "t.sqlite"
    report = build(out, docs=[("law", "273437"), ("law", "290833"), ("law", "284301")], links_dir=empty,
                   log=lambda m: None)
    db = sqlite3.connect(out)
    rows = db.execute("select f.name, t.name, l.evidence from link l join doc f on f.doc_id=l.from_doc"
                      " join doc t on t.doc_id=l.to_doc where l.kind='체계'").fetchall()
    assert rows == [("건축법 시행령", "건축법", "목적 조문")]
    assert report["hierarchy_missing"] == 2        # 건축법·농지법 — 체계도에도 없고 위임받은 법도 없다


def test_annex_items_and_fts(built):
    db, _ = built
    sid = _doc(db, "국토의 계획 및 이용에 관한 법률 시행령")
    aid = db.execute("select annex_id from annex where doc_id=? and no='17'", (sid,)).fetchone()[0]
    item = db.execute("select text from annex_item where annex_id=? and path='2/차'", (aid,)).fetchone()
    assert "제17호의 공장" in item[0]
    hits = db.execute("select rowid from fts_annex where fts_annex match ?", ('"첨단업종의공장"',)).fetchall()
    assert len(hits) >= 1


def test_report(built):
    _, report = built
    assert report["docs"] == len(SUBSET)
    assert "region_unmatched" in report and "plan_annex_missing" in report


def test_internal_annex_citation(built):
    db, _ = built
    sid = _doc(db, "국토의 계획 및 이용에 관한 법률 시행령")
    rows = db.execute("select to_path from link where from_doc=? and to_doc=? and from_path='별표17:2/차/1'",
                      (sid, sid)).fetchall()
    assert ("별표16:2/아",) in rows


def test_hang_header_with_children_keeps_citation(built):
    # 시행령 제71조 ①항은 호를 거느리지만 머리 문장의 "법 제76조제1항"은 링크돼야 한다
    db, _ = built
    sid = _doc(db, "국토의 계획 및 이용에 관한 법률 시행령")
    law = _doc(db, "국토의 계획 및 이용에 관한 법률")
    rows = db.execute("select to_path from link where from_doc=? and from_path='제71조/1' and to_doc=?",
                      (sid, law)).fetchall()
    assert ("제76조/1",) in rows


def test_metro_district_uses_metro_plan(built):
    db, _ = built
    seoul = _doc(db, "서울특별시 도시계획 조례")
    rows = db.execute("select doc_id from region_plan where region='서울특별시 강남구'").fetchall()
    assert rows[0][0] == seoul and len(rows) == 1


def _plan_of(db, region):
    return [n for (n,) in db.execute("select distinct d.name from region_plan r join doc d on d.doc_id=r.doc_id"
                                     " where r.region=?", (region,))]


def test_plan_ordinance_is_found_by_official_links_not_by_its_name(built):
    # 법제처 법령–조례 연계에서 시행령의 용도지역 조문에 이어진 조례가 계획 조례다 — 이름 꼴로 찾으면 '관리계획 조례'를 놓친다
    db, report = built
    assert _plan_of(db, "무안군") == ["무안군 관리계획 조례"]
    assert not any("무안군" in r for r in report["region_unmatched"])


def test_district_without_its_own_plan_uses_the_plan_its_ordinances_cite(built):
    db, _ = built
    assert _plan_of(db, "전남광주통합특별시 북구") == ["광주광역시 도시계획 조례"]


def test_district_citing_no_plan_follows_the_other_districts_of_its_sido(built):
    db, _ = built
    assert _plan_of(db, "전남광주통합특별시 남구") == ["광주광역시 도시계획 조례"]


def test_sido_whose_districts_have_no_plan_is_a_region_itself(built):
    # 자기 계획 조례를 가진 시군이 없는 시도(광역시·제주)는 시도 이름으로 찾는다 — 도(강원)는 시군이 자기 조례를 쓴다
    db, _ = built
    assert _plan_of(db, "제주특별자치도") == ["제주특별자치도 도시계획 조례"]
    assert _plan_of(db, "서울특별시") == ["서울특별시 도시계획 조례"]
    assert _plan_of(db, "강원특별자치도") == []


def test_special_self_governing_province_cities_keep_own_plan(built):
    # 강원·전북 '특별자치도' 의 시군은 자기 도시계획 조례를 쓴다 (제주만 도 조례)
    db, _ = built
    cc = _doc(db, "춘천시 도시계획 조례")
    assert db.execute("select doc_id from region_plan where region='춘천시'").fetchall() == [(cc,)]


def test_build_stops_when_the_zone_table_cannot_be_read(tmp_path, monkeypatch):
    # 시행령의 문구가 바뀌어 용도지역 표를 못 읽으면 빈 표로 끝내지 않고 멈춘다
    import re

    import indexer.build as b
    monkeypatch.setattr(b, "ZONE_RE", re.compile(r"^없는 문구$"))
    with pytest.raises(RuntimeError, match="용도지역 표"):
        build(tmp_path / "t.sqlite", docs=[("law", "288857")], log=lambda m: None)


def test_two_live_versions_of_one_ordinance_keep_the_latest(tmp_path):
    # 같은 지자체의 같은 이름 조례가 두 판 살아 있으면(목록에 옛 판이 남은 것) 시행일이 늦은 판만 색인한다
    out = tmp_path / "t.sqlite"
    report = build(out, docs=[("ordin", "789574"), ("ordin", "2014567")], log=lambda m: None)
    db = sqlite3.connect(out)
    assert db.execute("select mst from doc").fetchall() == [("2014567",)]
    assert report["same_name_older_dropped"] == 1


def test_citation_of_a_name_shared_by_two_regions_goes_to_the_citing_region(tmp_path):
    from indexer.build import pick_named
    org = {1: "경상남도 고성군", 2: "강원특별자치도 고성군", 3: "강원특별자치도 고성군", 4: "서울특별시"}
    assert pick_named([1, 2], 3, org) == 2          # 같은 지자체의 것
    assert pick_named([1, 2], 4, org) is None       # 가릴 수 없으면 잇지 않는다
    assert pick_named([1], 4, org) == 1
