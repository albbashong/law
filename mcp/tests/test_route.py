from law_mcp.engine.resolve import find_doc, find_region
from law_mcp.engine.route import route

NPA = "국토의 계획 및 이용에 관한 법률"
MUST = {"시작", "상위", "하위", "조례", "근거조문"}


def _doc(store, name):
    return find_doc(store, name)[0]["doc_id"]


def _route(store, law, path, region=None, steps_max=12):
    rid = find_region(store, region)[0]["doc_id"] if region else None
    return route(store, _doc(store, law), path, rid, 3, steps_max, MUST)


def test_route_from_building_coverage_reaches_national_planning_act(store):
    r = _route(store, "건축법", "제55조")
    hit = [s for s in r["단계"] if s["law"] == NPA and s["article"].startswith("제77조")]
    assert hit and hit[0]["꼭_열기"]


def test_route_without_region_reports_where_it_stopped(store):
    r = _route(store, NPA, "제77조")
    assert r["멈춤"]


def test_route_with_region_reaches_ordinance(store):
    r = _route(store, "국토계획법 시행령", "제84조", region="영천시")
    hit = [s for s in r["단계"] if s["law"] == "영천시 도시계획 조례"]
    assert hit and hit[0]["꼭_열기"]


def test_route_same_doc_definition_cite_is_not_must(store):
    r = _route(store, "건축법", "제11조")
    start = r["단계"][0]["law"]
    assert all(not s["꼭_열기"] for s in r["단계"] if s["law"] == start and s["관계"] == "인용")


def test_route_steps_link_back_to_where_they_came_from(store):
    r = _route(store, "건축법", "제55조")
    assert r["단계"][0]["관계"] == "시작" and r["단계"][0]["이어진"] is None
    assert all(s["이어진"] is not None and s["이어진"] < i for i, s in enumerate(r["단계"]) if i)


def test_route_caps_steps(store):
    r = _route(store, "건축법", "제11조", steps_max=3)
    assert len(r["단계"]) <= 3 and r["단계"][0]["관계"] == "시작"


def test_law_route_tool_unknown_law_fails_like_law_article(store):
    from law_mcp.tools.route import run_law_route
    assert run_law_route(store, "없는법률이름입니다", "제1조")["status"] == "failed"


def test_law_route_tool_gives_steps(store):
    from law_mcp.tools.route import run_law_route
    r = run_law_route(store, "건축법", "제55조")
    assert r["status"] == "ok" and r["facts"]["단계"][0]["law"] == "건축법"


def test_must_steps_form_a_chain_from_the_start(monkeypatch):
    """꼭 열기는 꼭 열 조문에서 이어진 것만 — 참고 인용 아래로 내려간 상하위는 꼭 열기가 아니다(리뷰 Important 2)."""
    from law_mcp.engine import route as rt
    from law_mcp.engine.follow import FollowResult, Ref

    def ref(doc, path, rel, depth, parent):
        return Ref(doc, path, f"법{doc}", path, "", "대통령령으로 정한다", "", rel, depth, parent)
    fake = FollowResult(refs=[ref(1, "제1조", "시작", 0, None), ref(1, "제2조", "인용", 1, (1, "제1조")),
                              ref(2, "제5조", "하위", 2, (1, "제2조")), ref(3, "제9조", "하위", 1, (1, "제1조"))])
    monkeypatch.setattr(rt, "follow", lambda *a, **k: fake)
    steps = rt.route(None, 1, "제1조", None, 3, 12, MUST)["단계"]
    assert [s["꼭_열기"] for s in steps] == [True, False, False, True]


def test_must_steps_stay_near_the_start(store):
    from law_mcp.engine.route import route
    steps = route(store, _doc(store, NPA), "제77조", None, 3, 30, MUST, must_depth=1)["단계"]
    depth = {}
    for i, s in enumerate(steps):
        depth[i] = 0 if s["이어진"] is None else depth[s["이어진"]] + 1
    assert all(depth[i] <= 1 for i, s in enumerate(steps) if s["꼭_열기"])


def test_lower_steps_are_must_only_under_a_delegating_article(monkeypatch):
    """하위·조례는 이끈 조문이 위임('대통령령으로 정한다')할 때만 꼭 열기 — 위임 없는 조문을 거꾸로 인용하는 하위 법령은 참고."""
    from law_mcp.engine import route as rt
    from law_mcp.engine.follow import FollowResult, Ref

    def run(start_text):
        refs = [Ref(1, "제1조", "법", "제1조", "", start_text, "", "시작", 0, None),
                Ref(2, "제5조", "법 시행령", "제5조", "", "", "", "하위", 1, (1, "제1조"))]
        monkeypatch.setattr(rt, "follow", lambda *a, **k: FollowResult(refs=refs))
        return [s["꼭_열기"] for s in rt.route(None, 1, "제1조", None, 3, 12, MUST)["단계"]]
    assert run("건폐율의 최대한도는 대통령령으로 정하는 기준에 따른다.") == [True, True]
    assert run("건폐율의 최대한도는 다른 법률에 따른다.") == [True, False]


def test_cross_law_definition_cite_is_not_must(monkeypatch):
    """다른 법령의 정의 조문 인용('"○○"이란 … 말한다')은 꼭 열기가 아니다(spec §5)."""
    from law_mcp.engine import route as rt
    from law_mcp.engine.follow import FollowResult, Ref
    refs = [Ref(1, "제1조", "법", "제1조", "", "", "", "시작", 0, None),
            Ref(2, "제2조/8", "다른법", "제2조제8호", "", "8. “산업단지”란 다음 각 목의 시설을 말한다.", "", "인용", 1, (1, "제1조")),
            Ref(3, "제77조", "또다른법", "제77조", "", "건폐율의 최대한도는 다음 범위에서 정한다.", "", "인용", 1, (1, "제1조"))]
    monkeypatch.setattr(rt, "follow", lambda *a, **k: FollowResult(refs=refs))
    assert [s["꼭_열기"] for s in rt.route(None, 1, "제1조", None, 3, 12, MUST)["단계"]] == [True, False, True]


def test_paragraphs_of_one_article_are_one_step(monkeypatch):
    """같은 조의 항·호는 한 단계 — law_article 로 조를 열면 함께 보인다(꼭 열기 줄이 항마다 늘지 않게)."""
    from law_mcp.engine import route as rt
    from law_mcp.engine.follow import FollowResult, Ref
    d = "대통령령으로 정한다"
    refs = [Ref(1, "제84조", "영", "제84조", "", d, "", "시작", 0, None),
            Ref(2, "제77조/1", "법", "제77조제1항", "", d, "", "상위", 1, (1, "제84조")),
            Ref(2, "제77조/3", "법", "제77조제3항", "", d, "", "상위", 1, (1, "제84조")),
            Ref(2, "제37조/4", "법", "제37조제4항", "", d, "", "상위", 1, (1, "제84조")),
            Ref(3, "제5조", "규칙", "제5조", "", "", "", "하위", 2, (2, "제77조/3"))]
    monkeypatch.setattr(rt, "follow", lambda *a, **k: FollowResult(refs=refs))
    steps = rt.route(None, 1, "제84조", None, 3, 12, MUST)["단계"]
    assert [(s["law"], s["article"]) for s in steps] == [("영", "제84조"), ("법", "제77조"), ("법", "제37조"), ("규칙", "제5조")]
    assert steps[3]["이어진"] == 1


def test_annex_cited_by_a_must_article_is_must(monkeypatch):
    """조문이 내용을 별표에 맡기면('별표 1에 따른 금액') 그 별표가 답이 있는 곳이다 — 같은 법령 안의 인용이어도 꼭 열기.
    참고 단계가 인용한 별표는 그대로 참고(실측 2026-11-18 청탁금지법 시행령 제17조 → 별표 1 음식물 5만원을 안 열고 답)."""
    from law_mcp.engine import route as rt
    from law_mcp.engine.follow import FollowResult, Ref
    refs = [Ref(1, "제17조", "영", "제17조", "", "가액 범위란 별표 1에 따른 금액을 말한다.", "", "시작", 0, None),
            Ref(1, "별표1", "영", "별표 1", "", "1. 음식물: 5만원", "", "인용", 1, (1, "제17조")),
            Ref(1, "제2조", "영", "제2조", "", "", "", "인용", 1, (1, "제17조")),
            Ref(1, "별표2", "영", "별표 2", "", "", "", "인용", 2, (1, "제2조"))]
    monkeypatch.setattr(rt, "follow", lambda *a, **k: FollowResult(refs=refs))
    steps = rt.route(None, 1, "제17조", None, 3, 12, MUST)["단계"]
    assert [(s["article"], s["꼭_열기"]) for s in steps] == [("제17조", True), ("별표 1", True), ("제2조", False), ("별표 2", False)]

