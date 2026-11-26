"""법의 핵심 조문 — 같은 법의 다른 조문이 가장 많이 인용하는 조문(낱말 없이 인용 관계만). 건축법이면 허가·신고·착공·
사용승인이 나와 절차를 묻는 질문의 단계가 된다(실측 2026-11-09 '서대문구에서 단독주택 짓으려면': 절차 조문을 하나도
조회하지 않았다)."""
from law_mcp.config import get_settings
from law_mcp.engine.core import core_articles
from law_mcp.tools.building_basis import call_building_basis


def test_upper_law_comes_from_the_official_hierarchy(store):
    enf, law = store.doc_named("건축법 시행령"), store.doc_named("건축법")
    assert store.uppers(enf["doc_id"]) == [law["doc_id"]]
    assert enf["doc_id"] in store.lowers(law["doc_id"]) and store.law_root(enf["doc_id"]) == law["doc_id"]


def test_use_table_is_found_by_its_title_not_by_number(store):
    from law_mcp.engine.uses import catalog, use_table
    t = use_table(store)
    assert (t["law"], t["no"]) == ("건축법 시행령", "1")
    assert "공장" in [c["name"] for c in catalog(store)]       # 호 이름은 법률의 용도 구분에서


def test_core_articles_are_the_most_cited_within_the_law_in_article_order(store):
    law = store.doc_named("건축법")
    refs = core_articles(store, law["doc_id"], 4)
    arts = [r.article for r in refs]
    assert "제11조" in arts and "제22조" in arts and len(arts) == 4
    nums = [int(a[1:].split("조")[0]) for a in arts]
    assert nums == sorted(nums)


def test_building_basis_names_the_core_articles_of_the_use_law(store):
    r = call_building_basis(store, "단독주택", region="영천시")
    core = r["facts"]["핵심_조문"]
    assert any(c.startswith("건축법 제11조") for c in core)
    assert "핵심_조문" in r["message"]


def test_building_basis_marks_the_core_articles_as_a_list_to_open(store):
    # 핵심_조문은 원문이 아니라 열어 볼 조문 이름 — 런타임이 이 칸만으로는 인용을 확인된 것으로 치지 않게 표시한다
    r = call_building_basis(store, "단독주택", region="영천시")
    assert r[get_settings().pointer_key] == ["핵심_조문"]


def test_building_basis_carries_the_short_names_of_the_laws_it_names(store):
    # 실측 2026-11-12 W3: 답이 결과에 정식 이름으로만 나온 법령을 약칭('국토계획법 시행령')으로 써서 지어낸 법령명으로 잡혔다
    # — law_article 처럼 결과에 나온 법령의 약칭을 함께 싣는다
    r = call_building_basis(store, "공장", region="영천시")
    assert "국토계획법시행령" in r["facts"]["약칭"]
