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


def test_hierarchy_by_name(built):
    db, _ = built
    sid = _doc(db, "건축법 시행령")
    law = _doc(db, "건축법")
    assert db.execute("select 1 from link where from_doc=? and to_doc=? and kind='체계'",
                      (sid, law)).fetchone()


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


def test_plan_name_with_rename_suffix():
    from indexer.build import PLAN_RE
    assert PLAN_RE.match("청도군 군계획 조례 [제명개정 2020. 10. 5.]").group("org") == "청도군"
    assert PLAN_RE.match("구례군계획 조례").group("org") == "구례"
    assert PLAN_RE.match("영천시 도시계획 조례").group("org") == "영천시"


def test_special_self_governing_province_cities_keep_own_plan(built):
    # 강원·전북 '특별자치도' 의 시군은 자기 도시계획 조례를 쓴다 (제주만 도 조례)
    db, _ = built
    cc = _doc(db, "춘천시 도시계획 조례")
    assert db.execute("select doc_id from region_plan where region='춘천시'").fetchall() == [(cc,)]
