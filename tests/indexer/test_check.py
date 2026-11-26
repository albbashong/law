"""새 색인 검사 — 비었거나 크게 줄어든 색인은 운영 DB 를 덮지 않는다."""
import sqlite3

from indexer.check import problems

SCHEMA = """CREATE TABLE doc(doc_id INTEGER PRIMARY KEY, source TEXT); CREATE TABLE unit(unit_id INTEGER PRIMARY KEY);
CREATE TABLE annex(annex_id INTEGER PRIMARY KEY, doc_id INT, title TEXT); CREATE TABLE annex_item(annex_id INT);
CREATE TABLE zone_map(zone TEXT); CREATE TABLE region_plan(region TEXT, doc_id INT); CREATE TABLE link(kind TEXT);"""


def _db(path, docs=10, zones=3, orphan=False):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO doc VALUES (?, 'law')", [(i,) for i in range(docs)])
    db.execute("INSERT INTO unit VALUES (1)")
    db.execute("INSERT INTO annex VALUES (1, 1, '용도별 건축물의 종류(제3조의5 관련)')")
    db.execute("INSERT INTO annex_item VALUES (?)", (99 if orphan else 1,))
    db.executemany("INSERT INTO zone_map VALUES (?)", [(str(i),) for i in range(zones)])
    db.execute("INSERT INTO region_plan VALUES ('영천시', 1)")
    db.executemany("INSERT INTO link VALUES (?)", [("조례연계",), ("인용",), ("체계",)])
    db.commit()
    db.close()
    return path


def test_sound_index_has_no_problems(tmp_path):
    assert problems(_db(tmp_path / "new"), _db(tmp_path / "old")) == []


def test_empty_zone_table_is_a_problem(tmp_path):
    # 기준 조문의 번호·문구가 바뀌면 용도지역 표가 빈 채로 빌드가 끝난다
    assert any("용도지역 표" in p for p in problems(_db(tmp_path / "new", zones=0)))


def test_index_much_smaller_than_the_previous_one_is_a_problem(tmp_path):
    out = problems(_db(tmp_path / "new", docs=5), _db(tmp_path / "old", docs=10))
    assert any(p.startswith("문서: 10 → 5") for p in out)


def test_annex_items_without_their_annex_are_a_problem(tmp_path):
    assert any("주인 없는" in p for p in problems(_db(tmp_path / "new", orphan=True)))
