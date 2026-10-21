import json

from law_mcp.engine.facts import pack
from law_mcp.engine.follow import follow
from law_mcp.engine.resolve import find_doc, find_region

ENF = "국토의 계획 및 이용에 관한 법률 시행령"


def _sid(store):
    return find_doc(store, "국토계획법 시행령")[0]["doc_id"]


def _rid(store):
    return find_region(store, "영천시")[0]["doc_id"]


def test_follow_down_to_ordinance(store):
    r = follow(store, _sid(store), "제84조/1", region_doc_id=_rid(store), direction="down", depth=2)
    assert any(x.law == "영천시 도시계획 조례" and x.path.startswith("제56조") for x in r.refs)


def test_follow_up_from_ordinance(store):
    r = follow(store, _rid(store), "제56조/-/16", region_doc_id=_rid(store), direction="up", depth=2)
    assert any(x.law == ENF and x.path.startswith("제84조") for x in r.refs)


def test_follow_up_reaches_law(store):
    # 조례 → 시행령 제84조 → 법 제77조 (2단계)
    r = follow(store, _rid(store), "제56조", region_doc_id=_rid(store), direction="up", depth=2)
    assert any(x.law == "국토의 계획 및 이용에 관한 법률" and x.path.startswith("제77조") for x in r.refs)


def test_follow_annex_up_to_article(store):
    # 시행령 별표 17은 제71조제1항제16호 관련
    r = follow(store, _sid(store), "별표17:2/차", region_doc_id=None, direction="up", depth=1)
    assert any(x.law == ENF and x.path.startswith("제71조") for x in r.refs)


def test_follow_cycle_and_budget(store):
    r = follow(store, _sid(store), "제71조", region_doc_id=None, direction="both", depth=5)
    refs, more = pack(r.refs, 7600)
    assert len(json.dumps(refs, ensure_ascii=False)) <= 7600
    assert len({(x.doc_id, x.path) for x in r.refs}) == len(r.refs)


def test_follow_needs_region(store):
    r = follow(store, _sid(store), "제84조/1", region_doc_id=None, direction="down", depth=2)
    assert any(m["이유"].startswith("조례 위임") for m in r.more)


def test_first_ref_is_start(store):
    r = follow(store, _sid(store), "제84조/1/16", region_doc_id=None, direction="none", depth=0)
    assert r.refs[0].relation == "시작" and r.refs[0].article == "제84조제1항제16호"


def test_noisy_ordjo_link_is_ignored(store):
    # lnkLsOrdJo 에는 영천시 조례 제33조 ↔ 공사중단 장기방치 건축물 정비법 같은 잡음이 있다.
    # 조례가 실제로 인용하지 않는 법령으로는 올라가지 않는다.
    r = follow(store, _rid(store), "제33조/-/16", region_doc_id=_rid(store), direction="up", depth=1)
    assert not any("공사중단" in x.law for x in r.refs)


def test_noisy_ordjo_link_ignored_downward(store):
    gd = find_doc(store, "공사중단 장기방치 건축물의 정비 등에 관한 특별조치법")[0]["doc_id"]
    r = follow(store, gd, "제12조의4", region_doc_id=_rid(store), direction="down", depth=1)
    assert not any(x.law == "영천시 도시계획 조례" for x in r.refs)


def test_down_only_same_law_family(store):
    law = find_doc(store, "건축법")[0]["doc_id"]
    r = follow(store, law, "제11조", region_doc_id=None, direction="down", depth=1)
    lower = [x for x in r.refs if x.relation == "하위"]
    assert lower and all(x.law.startswith("건축법") for x in lower)
    assert any(x.law == "건축법 시행령" and x.path.startswith("제8조") for x in lower)


def test_ordjo_requires_article_level_citation(store):
    # 영천시 조례 제56조는 산집법을 인용하지 않는다 — 문서 단위가 아니라 조문 단위로 거른다
    r = follow(store, _rid(store), "제56조", region_doc_id=_rid(store), direction="up", depth=1)
    assert not any("산업집적" in x.law or "공사중단" in x.law for x in r.refs)


def test_both_direction_keeps_lower_law_before_lateral_citations(store):
    law = find_doc(store, "건축법")[0]["doc_id"]
    r = follow(store, law, "제11조", region_doc_id=None, direction="both", depth=1)
    assert any(x.law == "건축법 시행령" and x.path.startswith("제8조") for x in r.refs)


# --- 의제("…를 받은 것으로 본다") ---

def _doc(store, name):
    return store.doc_named(name)["doc_id"]


def test_deeming_clause_marks_target_as_deemed(store):
    # 건축법 제11조제5항: 건축허가를 받으면 각 호(개발행위허가 등)를 받은 것으로 본다
    r = follow(store, _doc(store, "건축법"), "제11조/5", None, "up", 1)
    rels = {(x.law, x.path.split("/")[0]): x.relation for x in r.refs[1:]}
    assert rels.get(("국토의 계획 및 이용에 관한 법률", "제56조")) == "의제"


def test_permit_deemed_by_other_law_found_downward(store):
    # 개발행위허가(제56조)에서 아래로: 다른 법률의 허가로 의제되는 경우가 나온다
    r = follow(store, _doc(store, "국토의 계획 및 이용에 관한 법률"), "제56조", None, "down", 1)
    hit = [x for x in r.refs[1:] if x.law == "건축법" and x.path.startswith("제11조/5")]
    assert hit and hit[0].relation == "의제됨"
    assert "건축법" in hit[0].note and "제11조" in hit[0].note


def test_citation_in_deeming_header_is_not_a_deemed_item(store):
    # ⑤ 머리 문장의 "공장건축물은 산업집적법 제13조의2·제14조에 따라 …"는 의제 목록(각 호)이 아니다
    r = follow(store, _doc(store, "건축법"), "제11조/5", None, "up", 1)
    deemed = {(x.law, x.path.split("/")[0]) for x in r.refs[1:] if x.relation == "의제"}
    assert ("산업집적활성화 및 공장설립에 관한 법률", "제13조의2") not in deemed
    assert ("국토의 계획 및 이용에 관한 법률", "제56조") in deemed
