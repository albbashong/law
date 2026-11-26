"""법령체계도에서 상하위를 읽는다 — 이름 꼴('… 시행령')로 짐작하지 않는다."""
from indexer.hierarchy import law_nodes, relatives


def _info(law_id, name):
    return f"<기본정보><법령ID>{law_id}</법령ID><법령명><![CDATA[{name}]]></법령명></기본정보>"


TREE = ("<법령체계도>" + _info("L1", "건축법") + "<상하위법><법률>" + _info("L1", "건축법")
        + "<시행령>" + _info("D1", "건축법 시행령")
        + "<시행규칙>" + _info("R1", "건축법 시행규칙") + "</시행규칙>"
        + "<시행규칙>" + _info("R2", "건축물의 설비기준 등에 관한 규칙") + "</시행규칙>"
        + "<행정규칙><고시>" + _info("X9", "어느 고시") + "</고시></행정규칙>"
        + "<자치법규><조례>" + _info("O9", "어느 조례") + "</조례></자치법규>"
        + "</시행령></법률></상하위법></법령체계도>")


def _nodes(xml):
    return {n.law_id: n for n in law_nodes(xml)}


def test_parents_follow_the_nesting_of_the_tree():
    n = _nodes(TREE)
    assert set(n) == {"L1", "D1", "R1", "R2"}        # 행정규칙·자치법규 가지는 법령이 아니다
    assert (n["L1"].parent, n["D1"].parent, n["R1"].parent) == (None, "L1", "D1")
    assert n["R2"].tier == "시행규칙" and n["R2"].parent == "D1"   # 이름에 '시행규칙'이 없어도 하위 법령


def test_decree_without_an_act_hangs_from_no_parent():
    xml = ("<법령체계도><상하위법><법률><시행령>" + _info("D7", "어느 규정")
           + "<시행규칙>" + _info("R7", "어느 규정 시행규칙") + "</시행규칙></시행령></법률></상하위법></법령체계도>")
    n = _nodes(xml)
    assert n["D7"].parent is None and n["R7"].parent == "D7"


def test_broken_xml_gives_nothing():
    assert law_nodes("<법령체계도><상하위법>") == []


def test_abbreviations_point_up_to_self_and_down_only_when_single():
    rel = relatives(_nodes(TREE))
    assert rel["R2"] == {"규칙": "R2", "영": "D1", "법": "L1"}
    assert rel["D1"] == {"영": "D1", "법": "L1", "규칙": "R1"}    # 규칙이 둘이면 이름이 "건축법 시행규칙"인 것
    assert rel["L1"] == {"법": "L1", "영": "D1", "규칙": "R1"}


def test_rule_hung_directly_under_the_act_does_not_hide_the_enforcement_rule():
    # 법률 바로 밑에 다른 규칙이 하나 있고, 진짜 시행규칙은 시행령 밑에 있다(국토계획법) — 얕은 쪽 하나를 고르지 않는다
    xml = ("<법령체계도><상하위법><법률>" + _info("L1", "국토법") + "<시행규칙>" + _info("X1", "지하보도 기준 규칙")
           + "</시행규칙><시행령>" + _info("D1", "국토법 시행령") + "<시행규칙>" + _info("R1", "국토법 시행규칙")
           + "</시행규칙></시행령></법률></상하위법></법령체계도>")
    assert relatives(_nodes(xml))["L1"] == {"법": "L1", "영": "D1", "규칙": "R1"}
