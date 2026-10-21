"""갱신 — 목록을 다시 훑으면 사라진(개정·폐지된) 판을 표시하고 색인에서 뺀다. 별표를 서식으로 올린 문서,
별표 파일들을 zip 으로 묶어 올린 첨부도 텍스트를 얻는다."""
import gzip
import io
import sqlite3
import zipfile

import fetch_annex
import fetch_lawdata
from annex_text import extract


def _rows(*msts):
    return [(m, f"id{m}", f"조례{m}", "기관", "조례", None, "20260101", "20260101", "제정", None) for m in msts]


def test_relist_marks_versions_no_longer_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_lawdata, "ROOT", tmp_path)
    st = fetch_lawdata.Store("ordin")
    pages = {"now": _rows("1", "2")}
    monkeypatch.setattr(fetch_lawdata, "list_page", lambda oc, target, page: (2, pages["now"]))
    fetch_lawdata.do_list(st, "oc", 1, relist=False)
    pages["now"] = _rows("1", "3")                        # 2 는 개정돼 3 으로 바뀌었다
    fetch_lawdata.do_list(st, "oc", 1, relist=True)
    gone = dict(st.db.execute("SELECT mst, superseded FROM ordin").fetchall())
    assert gone == {"1": 0, "2": 1, "3": 0}


def test_interrupted_relist_marks_nothing(tmp_path, monkeypatch):
    """목록을 끝까지 못 훑었으면 안 보인 판이 사라진 것인지 알 수 없다."""
    monkeypatch.setattr(fetch_lawdata, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_lawdata, "PAGE_SIZE", 1)
    st = fetch_lawdata.Store("ordin")
    monkeypatch.setattr(fetch_lawdata, "list_page", lambda oc, target, page: (2, _rows(str(page))))
    fetch_lawdata.do_list(st, "oc", 1, relist=False)

    def broken(oc, target, page):
        if page == 2:
            raise OSError("끊김")
        return 2, _rows("1")
    monkeypatch.setattr(fetch_lawdata, "list_page", broken)
    try:
        fetch_lawdata.do_list(st, "oc", 1, relist=True)
    except OSError:
        pass
    assert {s for (s,) in st.db.execute("SELECT superseded FROM ordin")} == {0}


def test_build_skips_superseded_versions(tmp_path, monkeypatch):
    import indexer.build as build
    monkeypatch.setattr(fetch_lawdata, "ROOT", tmp_path)
    st = fetch_lawdata.Store("ordin")
    st.db.executemany("INSERT INTO ordin (mst, name, fetched, superseded) VALUES (?,?,1,?)",
                      [("1", "현행", 0), ("2", "옛판", 1)])
    st.db.commit()
    fetch_lawdata.Store("law")
    monkeypatch.setattr(build, "DATA", tmp_path / "data")
    assert [r["mst"] for _, r in build._load_sources(None)] == ["1"]


def _xml(units):
    body = "".join(f"<별표단위><별표번호>{no}</별표번호><별표가지번호>00</별표가지번호><별표구분>{g}</별표구분>"
                   f"<별표제목>{t}</별표제목><별표첨부파일명>https://x/{no}</별표첨부파일명><별표내용> </별표내용></별표단위>"
                   for no, g, t in units)
    return ("<자치법규><조문><조내용>자연녹지지역 안에서 건축할 수 있는 건축물: 별표 16과 같다.</조내용></조문>"
            f"<별표>{body}</별표></자치법규>").encode()


def _scan(tmp_path, monkeypatch, xml):
    monkeypatch.setattr(fetch_annex, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_lawdata, "ROOT", tmp_path)
    st = fetch_lawdata.Store("ordin")
    st.db.execute("INSERT INTO ordin (mst, name, fetched) VALUES ('100', '시 도시계획 조례', 1)")
    st.db.commit()
    p = tmp_path / "data" / "ordin" / "xml" / "100" / "100.xml.gz"
    p.parent.mkdir(parents=True)
    p.write_bytes(gzip.compress(xml))
    db = fetch_annex.open_db("ordin")
    fetch_annex.do_scan(db, "ordin")
    return db.execute("SELECT no, gubun, status FROM annex WHERE mst='100'").fetchall()


def test_forms_are_scanned_when_the_body_cites_annexes_but_none_are_listed(tmp_path, monkeypatch):
    rows = _scan(tmp_path, monkeypatch, _xml([("0001", "서식", "시 도시계획 조례")]))
    assert rows == [("0001", "서식", fetch_annex.PENDING)]


def test_forms_stay_out_when_real_annexes_exist(tmp_path, monkeypatch):
    rows = _scan(tmp_path, monkeypatch, _xml([("0001", "별표", "[별표 1] 기준"), ("0002", "서식", "신청서")]))
    assert [r[0] for r in rows] == ["0001"]


def test_zip_of_annex_files_is_read_member_by_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[별표2] 생산녹지지역안에서 건축할 수 있는 건축물.txt", "생산녹지 내용".encode())
        z.writestr("[별표1] 보전녹지지역안에서 건축할 수 있는 건축물.txt", "보전녹지 내용".encode())
    kind, text = extract(buf.getvalue())
    assert kind == "zip"
    assert text.index("[별표1] 보전녹지지역") < text.index("보전녹지 내용") < text.index("[별표2] 생산녹지지역")
