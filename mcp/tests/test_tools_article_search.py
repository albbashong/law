import json

from law_mcp.tools.article import run_law_article
from law_mcp.store.db import nospace
from law_mcp.tools.search import run_law_search


def test_article_with_chain(store):
    r = run_law_article(store, "영천시 도시계획 조례", "제56조제16호", chain="up")
    assert r["status"] == "ok"
    assert "자연녹지지역" in r["facts"]["원문"]
    assert any(x["law"].endswith("시행령") and x["article"].startswith("제84조") for x in r["facts"]["refs"])


def test_article_annex_item(store):
    r = run_law_article(store, "국토계획법 시행령", "별표 17 제2호 차목", chain="none")
    assert r["status"] == "ok" and "제17호의 공장" in r["facts"]["원문"]


def test_article_unknown_law_suggests(store):
    r = run_law_article(store, "국토계획법 시행녕", "제1조")
    assert r["status"] == "failed" and r["facts"]["제안"]


def test_article_fixes_a_one_letter_slip_in_the_law_name(store):
    # 이름이 한 글자만 다르고 가장 가까운 법령이 뚜렷하면 그 법령으로 조회하고, 보정했다고 밝힌다
    r = run_law_article(store, "국토의 계획 및 이용에 관헌 법률", "제76조")
    assert r["status"] in ("ok", "partial")
    assert r["facts"]["법령"] == "국토의 계획 및 이용에 관한 법률"
    assert "국토의 계획 및 이용에 관헌 법률" in r["facts"]["법령명_보정"]
    # 멀면 고치지 않는다
    assert run_law_article(store, "없는법률이름입니다", "제1조")["status"] == "failed"


def test_article_missing_article(store):
    r = run_law_article(store, "건축법", "제999조")
    assert r["status"] == "failed" and "제999조" in r["message"]


def test_article_down_needs_region_hint(store):
    r = run_law_article(store, "국토계획법 시행령", "제84조제1항", chain="down")
    assert any("지역" in m["이유"] for m in r["facts"]["더_볼_연결"])


def test_article_down_with_region_finds_ordinance(store):
    r = run_law_article(store, "국토계획법 시행령", "제84조제1항", region="영천시", chain="down")
    assert any(x["law"] == "영천시 도시계획 조례" for x in r["facts"]["refs"])


def test_search_region_filter(store):
    r = run_law_search(store, "자연녹지지역 건폐율", scope="ordin", region="영천시")
    assert r["status"] == "ok"
    res = r["facts"]["결과"]
    assert res and all(x["law"].startswith("영천시") for x in res)
    assert res[0]["호출인자"]["law"].startswith("영천시")


def test_search_law_scope(store):
    r = run_law_search(store, "첨단업종의 공장", scope="law")
    assert any(x["law"] == "국토의 계획 및 이용에 관한 법률 시행령" for x in r["facts"]["결과"])


def test_search_too_short(store):
    r = run_law_search(store, "공장", scope="law")
    assert r["status"] in ("ok", "failed")   # 2글자도 예외 없이 처리


def test_facts_size(store):
    r = run_law_article(store, "국토계획법 시행령", "제71조", chain="both", depth=3)
    assert len(json.dumps(r["facts"], ensure_ascii=False)) <= 7600


def test_fit_moves_refs_to_more():
    from law_mcp.engine.facts import fit, size
    facts = {"refs": [{"law": "법", "article": f"제{i}조", "excerpt": "가" * 300} for i in range(40)],
             "더_볼_연결": []}
    fit(facts, 2000)
    assert size(facts) <= 2000 and facts["더_볼_연결"][0]["이유"].startswith("응답 크기")


def test_search_spaced_short_words_fallback(store):
    # "자연 녹지 지역 건폐율" 처럼 2글자 단어로 띄어 쓰면 단어별 trigram 이 안 된다 → 이어 붙여 다시 찾는다
    r = run_law_search(store, "자연 녹지 건폐율", scope="ordin", region="영천시")
    assert r["status"] == "ok" and r["facts"]["결과"], r["message"]


def test_search_points_to_building_basis_when_zone_and_use_present(store):
    # 용도지역(zone_map)과 건축물 용도(별표 1)를 데이터로 알아보면 판정 도구를 권한다
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, "계획관리지역 논에 축사 건축 제한")
    hint = r["facts"].get("먼저_부를_도구")
    assert hint and hint["tool"] == "building_basis"
    assert hint["args"]["zone"] == "계획관리지역" and hint["args"]["use"] == "축사"
    assert "building_basis" in r["message"]


def test_search_hint_reads_a_use_name_split_by_a_space(store):
    """'제2종 근린생활시설'처럼 띄어 쓴 용도명도 이웃 낱말을 이어 별표 1 이름으로 알아본다."""
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, "제2종 근린생활시설 계획관리지역 영천시", region="영천시")
    hint = r["facts"].get("먼저_부를_도구")
    assert hint and hint["args"]["use"] == "제2종 근린생활시설" and hint["args"]["zone"] == "계획관리지역"


def test_two_letter_terms_are_found_in_titles(store):
    """trigram 색인은 3글자 이상만 찾는다 — '산지·전용·기준'처럼 2글자 법령 용어는 조문·별표 제목에서 찾는다."""
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, "산지 전용 기준")
    first = r["facts"]["결과"][0]
    assert (first["law"], first["article"]) == ("산지관리법", "제18조")


def test_title_hits_join_text_hits_for_two_letter_words(store):
    from law_mcp.tools.search import run_law_search
    rows = run_law_search(store, "농지 전용 신고")["facts"]["결과"]
    assert ("농지법", "제35조") in [(x["law"], x["article"]) for x in rows[:3]]


def test_rare_title_word_outweighs_common_ones(store):
    """제목 점수는 낱말이 드물수록 크다 — 흔한 '기준·허가'보다 드문 '산지전용'이 든 제목이 앞선다."""
    from law_mcp.tools.search import run_law_search
    rows = run_law_search(store, "산지전용 허가 기준 사항")["facts"]["결과"]
    assert rows[0]["law"].startswith("산지관리법")


def test_named_law_is_searched_inside_including_annex_text(store):
    """질의에 법령명이 있으면 그 법령 안에서 나머지 낱말을 본문·별표까지 찾는다(제목에 없어도)."""
    from law_mcp.tools.search import run_law_search
    rows = run_law_search(store, "건축법 시행령 다가구주택")["facts"]["결과"]
    assert rows[0]["law"] == "건축법 시행령" and "다가구주택" in rows[0]["snippet"].replace(" ", "")


def test_definition_hint_reads_the_deung_form_of_a_word(store):
    """법령은 '공개공지등'처럼 '…등'을 붙여 용어를 정의한다 — '공개공지'로 물어도 그 정의를 권한다."""
    from law_mcp.tools.search import _definition_hint
    assert _definition_hint(store, "공개공지 설치 기준") == {"tool": "law_definition", "args": {"term": "공개공지등"}}


def test_definition_hint_prefers_the_more_specific_term(store):
    """여러 낱말이 정의된 용어면 정의한 법령이 적은(더 특정한) 용어를 권한다."""
    from law_mcp.tools.search import _definition_hint
    assert _definition_hint(store, "시ㆍ도지사 공개공지 기준")["args"]["term"] == "공개공지등"


def test_search_without_zone_has_no_hint(store):
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, "농지의 전용허가")
    assert "먼저_부를_도구" not in r["facts"]


def test_article_lists_all_deemed_permits(store):
    # 의제 조항을 조회하면 각 호의 의제 대상 전체를 짧은 목록으로 준다(이웃 개수 제한과 무관)
    from law_mcp.tools.article import run_law_article
    r = run_law_article(store, "건축법", "제11조제5항")
    items = r["facts"]["의제_목록"]
    assert any("국토의 계획 및 이용에 관한 법률 제56조" in i for i in items)
    assert any("농지법 제34조" in i for i in items) and any("산지관리법 제14조" in i for i in items)
    assert items[0].startswith("제1호")


def test_search_points_to_definition_for_defined_term(store):
    # 질의에 법률이 정의한 용어가 있으면(정의 색인) law_definition 을 권한다
    from law_mcp.tools.search import run_law_search
    r = run_law_search(store, "용도지역의 정의")
    hint = r["facts"].get("용어_정의")
    assert hint == {"tool": "law_definition", "args": {"term": "용도지역"}}


def test_definition_hint_only_strips_particles(store):
    # "용도지역의"는 용도지역 + 조사지만, "농지전용허가"의 앞부분 "농지"는 다른 낱말의 일부다
    from law_mcp.tools.search import run_law_search
    assert run_law_search(store, "농지전용허가 기준")["facts"].get("용어_정의") is None


def test_search_ranks_articles_titled_by_term_first(store):
    # 일치 건수가 많아도 저장 순서로 자르지 않는다 — 제목에 검색어가 든 조문, 그런 조문이 많은 법령이 앞에 온다
    r = run_law_search(store, "농지전용", scope="law", limit=5)
    top = r["facts"]["결과"][:2]          # 픽스처에서 제목에 든 법령은 농지법 하나 — 법령당 2건
    assert all("농지전용" in nospace(x["title"]) for x in top), top
    assert top[0]["law"].startswith("농지법"), top


def test_search_spreads_results_across_laws(store):
    # 한 법령이 결과를 독차지하지 않는다 — 법령마다 몇 건씩만
    from collections import Counter
    r = run_law_search(store, "개발행위", scope="law", limit=8)
    per_law = Counter(x["law"] for x in r["facts"]["결과"] if not x["article"].startswith("별표"))
    assert per_law and max(per_law.values()) <= 3, per_law


def test_search_falls_back_to_long_word_when_phrase_misses(store):
    # "농지전용 조건" → 2글자 '조건'이 붙어 "농지전용조건" 한 덩어리로만 찾으면 0건 — 긴 낱말만으로 다시 찾는다
    r = run_law_search(store, "농지전용 조건", scope="law")
    assert r["facts"]["결과"], r["message"]


def test_search_prefers_the_article_others_cite_within_a_law(store):
    # 같은 법령 안에서는 다른 조문·법령이 많이 가리키는 조문(그 제도의 본 조문)이 앞에 온다
    r = run_law_search(store, "개발행위", scope="law", limit=8)
    got = [(x["law"], x["article"]) for x in r["facts"]["결과"]]
    assert ("국토의 계획 및 이용에 관한 법률", "제56조") in got, got


def test_search_tops_up_a_thin_precise_result_with_broader_matches(store):
    # 정확한 검색식이 1건뿐이면 거기서 멈추지 않고 넓은 검색식으로 채운다 — 정확한 것이 앞에 남는다
    precise = run_law_search(store, "개발행위 허가기준", scope="law", limit=8)["facts"]["결과"]
    units = [x for x in precise if not x["article"].startswith("별표")]
    assert len(units) >= 6, units


def test_snippet_centers_on_the_query_word_the_title_lacks():
    from law_mcp.tools.search import _focus_snippet
    text = "제25조(수의계약에 의할 수 있는 경우) ① 다음 각 호의 경우에는 수의계약을 할 수 있다. " + "가" * 300 + \
           " 나. 추정가격이 2천만원 이하인 물품의 제조ㆍ구매계약"
    snip = _focus_snippet(text, "수의계약 추정가격", "수의계약에 의할 수 있는 경우")
    assert "2천만원" in snip


def test_snippet_falls_back_to_the_title_word_when_others_are_absent():
    from law_mcp.tools.search import _focus_snippet
    text = "제25조(수의계약에 의할 수 있는 경우) ① 수의계약을 할 수 있다."
    assert "수의계약" in _focus_snippet(text, "수의계약 금액", "수의계약에 의할 수 있는 경우")


def test_article_result_names_itself_as_a_law_article_pair(store):
    r = run_law_article(store, "국토계획법 시행령", "별표 17 제2호 차목", chain="none")
    assert r["facts"]["호출인자"] == {"law": r["facts"]["법령"], "article": r["facts"]["조문"]}


# ── 조문 구조 — 각 호·목이 별개 사유인지 함께 충족할 요건인지를 원문의 연결 문구에서 읽어 사실로 싣는다 ──

def test_article_structure_marks_alternative_items(store):
    r = run_law_article(store, "건축법", "제70조", chain="none")
    s = r["facts"]["구조"][0]
    assert s["조항"] == "제70조" and "어느 하나" in s["연결"]
    assert "별개" in s["관계"] and s["항목"][0].startswith("제70조제1호")


def test_article_structure_marks_conjunctive_items(store):
    r = run_law_article(store, "건축법 시행령", "제15조의3", chain="none")
    s = r["facts"]["구조"][0]
    assert "모두" in s["연결"] and "모두 충족" in s["관계"]


def test_article_structure_covers_only_the_opened_part(store):
    whole = run_law_article(store, "국토계획법", "제56조", chain="none")["facts"]["구조"]
    part = run_law_article(store, "국토계획법", "제56조제1항", chain="none")["facts"]["구조"]
    assert part and all(e["조항"].startswith("제56조제1항") for e in part)
    assert len(whole) >= len(part)


def test_article_structure_reads_each_parents_own_lead(store):
    """호의 연결 문구("다음 각 목의 어느 하나")를 조문 전체의 관계로 잘못 읽지 않는다."""
    s = {e["조항"]: e for e in run_law_article(store, "건축법 시행령", "제15조의3", chain="none")["facts"]["구조"]}
    assert "모두" in s["제15조의3"]["연결"] and "어느 하나" in s["제15조의3제1호"]["연결"]


# ── 조항을 열면 하위 호·목 본문까지 — 세율·기준이 목에 적힌 경우가 많다 ─────────────────────

def test_opening_a_paragraph_includes_its_items(store):
    r = run_law_article(store, "국토계획법", "제56조제1항", chain="none")
    text = r["facts"]["원문"].replace(" ", "")
    assert "다음각호의어느하나" in text and "1.건축물의건축" in text


def test_images_in_law_text_are_marked_not_left_as_tags():
    from law_mcp.engine.facts import clean_text
    t = clean_text('세율 <img src="http://www.law.go.kr/x.gif" alt="표"> 끝')
    assert "<img" not in t and "이미지" in t and t.startswith("세율") and t.endswith("끝")


def test_article_lists_the_law_abbreviations(store):
    """답에 약칭(국토계획법)을 써도 조회 결과로 확인되게 — 법령명 검증이 약칭을 지어낸 이름으로 보지 않는다."""
    r = run_law_article(store, "국토의 계획 및 이용에 관한 법률 시행령", "제84조제1항", chain="none")
    assert "국토계획법시행령" in r["facts"]["약칭"]
    assert nospace(r["facts"]["법령"]) not in r["facts"]["약칭"]


def test_law_found_by_both_searches_keeps_its_place_ahead_of_ordinances():
    """본문 검색과 제목 검색이 함께 찾은 법령 조문이 중복으로 빠져 조례 뒤로 밀리지 않는다."""
    from law_mcp.tools.search import _merge
    row = lambda law, art: {"law": law, "article": art}   # noqa: E731
    text = [row("가시 건축 조례", "제30조"), row("건축법", "제44조")]
    titled = [row("건축법", "제44조"), row("나시 건축 조례", "제31조"), row("가시 건축 조례", "제30조")]
    out = _merge(text, titled, [], short=True)
    assert [(r["law"], r["article"]) for r in out] == [
        ("건축법", "제44조"), ("나시 건축 조례", "제31조"), ("가시 건축 조례", "제30조")]


def test_three_letter_law_name_in_query_is_searched_inside(store):
    """'건축법'처럼 세 글자 법령명도 질의에 있으면 그 법령 안에서 찾고, 제목에 낱말이 든 조문을 앞에 둔다."""
    from law_mcp.tools.search import run_law_search
    rows = run_law_search(store, "건축법 적용 제외")["facts"]["결과"]
    assert (rows[0]["law"], rows[0]["article"]) == ("건축법", "제3조"), rows[:4]


def test_opening_a_paragraph_lists_its_siblings(store):
    """항·호 하나만 열어도 같은 조의 다른 항(위로 올라가며 각 단계의 형제)이 첫머리와 함께 보인다 — 다른 갈래를 놓치지 않게."""
    r = run_law_article(store, "건축법 시행령", "제28조제1항", chain="none")
    near = " ".join(r["facts"]["같은_조_다른_항목"])
    assert "제28조제2항" in near and "연면적" in near
    assert "제28조제1항:" not in near


def test_whole_article_has_no_sibling_list(store):
    r = run_law_article(store, "건축법 시행령", "제28조", chain="none")
    assert "같은_조_다른_항목" not in r["facts"]


def test_two_letter_word_found_in_body_text_beside_a_common_word(store):
    # 2글자 낱말이 제목엔 없고 본문에만 있으면 trigram 으로 못 찾아 흔한 낱말(허가)의 조문만 나왔다
    # — 색인 낱말 목록에서 그 낱말로 시작하는 3글자 조각을 찾아 본문에서 찾는다
    rows = run_law_search(store, "묘지 허가")["facts"]["결과"]
    assert any("묘지" in r["snippet"] for r in rows[:5]), [r["article"] for r in rows]


def test_index_grams_of_a_two_letter_word(store):
    grams = store.index_grams("묘지")
    assert grams and all(g.startswith("묘지") and len(g) == 3 for g in grams)
    assert store.index_grams("없는말") == ["없는말"]


def test_search_says_which_query_word_no_result_contains(store):
    # 법령이 쓰지 않는 일상어(헬멧)는 찾을 수 없다 — 그 낱말이 결과 어디에도 없다고 사실을 알린다
    r = run_law_search(store, "헬멧 건축물")
    assert "헬멧" in r["message"] and "용어" in r["message"]
    r = run_law_search(store, "자연녹지지역 공장")
    assert "나오지 않습니다" not in r["message"]


def test_ordinance_article_citing_no_law_goes_up_through_the_official_link(store):
    # 위원회 구성처럼 법령을 직접 인용하지 않는 조례 조문 — 법제처 연계가 유일한 상위 단서다
    r = run_law_article(store, "서울특별시 도시계획 조례", "제54조")
    ups = [(x["law"], x["article"], x["note"]) for x in r["facts"]["refs"]]
    assert any(law == "국토의 계획 및 이용에 관한 법률" and "법제처 연계" in note for law, _, note in ups), ups


def test_other_family_law_cited_by_a_decree_is_not_its_upper_law(store):
    # 건축법 시행령이 국토계획법을 인용해도 위임 근거(상위)는 건축법 쪽이다
    r = run_law_article(store, "건축법 시행령", "제107조", depth=1)
    notes = {(x["law"], x["note"]) for x in r["facts"]["refs"]}
    assert ("국토의 계획 및 이용에 관한 법률", "상위") not in notes, notes
    assert any(law == "건축법" and note == "상위" for law, note in notes), notes


def test_article_lists_abbreviations_of_linked_laws_too(store):
    """연결로 함께 나온 하위 법령의 약칭도 — 법률만 열고 답에 「국토계획법 시행령」이라 써도 지어낸 이름이 아니다."""
    r = run_law_article(store, "국토의 계획 및 이용에 관한 법률", "제76조", chain="down")
    linked = {x["law"] for x in r["facts"]["refs"]}
    assert "국토의 계획 및 이용에 관한 법률 시행령" in linked
    assert "국토계획법시행령" in r["facts"]["약칭"]
