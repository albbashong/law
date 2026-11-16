"""agent/search_plan/query.py — 검색 → LLM 고르기 1회 → (주소면) 필지 지역 → law_route 로 검색 계획."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

QUERY = Path(__file__).resolve().parents[2] / "search_plan" / "query.py"


@pytest.fixture
def sp(monkeypatch, tmp_path):
    (tmp_path / "select_prompt.txt").write_text("후보 중 시작 조문 번호를 JSON 으로", encoding="utf-8")
    monkeypatch.setenv("PLAN_AGENT_DIR", str(tmp_path))
    for k, v in {"PLAN_KEY": "검색_계획", "PLAN_SEARCH_TOOL": "law_search", "PLAN_ROUTE_TOOL": "law_route",
                 "PLAN_PARCEL_TOOL": "parcel_zoning", "PLAN_REGION_KEYS": "시도,시군구",
                 "PLAN_SELECT_PROMPT": "select_prompt.txt", "PLAN_PICK_KEY": "시작", "PLAN_ADDRESS_KEY": "주소",
                 "PLAN_START_MAX": "3", "PLAN_STEPS_MAX": "16", "PLAN_SEARCH_LIMIT": "20"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("sp_query", QUERY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _step(law, article, rel="시작", must=True, back=None):
    return {"law": law, "article": article, "관계": rel, "꼭_열기": must, "이어진": back}


class FakeMcp:
    def __init__(self, results):
        self.results, self.calls = results, []

    async def call(self, name, args):
        self.calls.append((name, args))
        r = self.results[name]
        if isinstance(r, Exception):
            raise r
        return r(args) if callable(r) else r


class FakeModel:
    def __init__(self, answer):
        self.answer, self.seen = answer, []

    async def next_step(self, messages, tools, require=False):
        self.seen.append(messages)
        return {"answer": self.answer, "usage": {"tokens_in": 1, "tokens_out": 1}}


SEARCH = {"status": "ok", "facts": {"결과": [
    {"law": "A법", "article": "제1조", "title": "t", "snippet": "s", "호출인자": {"law": "A법", "article": "제1조"}},
    {"law": "B법", "article": "제2조", "title": "t", "snippet": "s", "호출인자": {"law": "B법", "article": "제2조"}},
]}}


def _route(args):
    return {"status": "ok", "facts": {"단계": [_step(args["law"], args["article"]),
                                             _step("C법", "제3조", "상위", True, 0)], "멈춤": []}}


def _plan(out):
    return out["tool_results"]["search_plan"]["facts"]["검색_계획"]


def test_plan_follows_search_pick_and_route(sp):
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route})
    out = asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[1],"주소":null}')))
    assert ("law_route", {"law": "B법", "article": "제2조", "region": None}) in mcp.calls
    assert [(s["law"], s["article"]) for s in _plan(out)["단계"]] == [("B법", "제2조"), ("C법", "제3조")]
    assert out["stopped"] == "answer"


def test_candidates_are_numbered_for_the_model(sp):
    model = FakeModel('{"시작":[0]}')
    asyncio.run(sp.run_query("질문", mcp=FakeMcp({"law_search": SEARCH, "law_route": _route}), model=model))
    shown = json.dumps(model.seen[0], ensure_ascii=False)
    assert "0" in shown and "A법 제1조" in shown and "B법 제2조" in shown


def test_address_is_resolved_to_region_before_routing(sp):
    parcel = {"status": "ok", "facts": {"시도": "경상북도", "시군구": "영천시"}}
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route, "parcel_zoning": parcel})
    out = asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[0],"주소":"영천시 금호읍 신월리 1-51"}')))
    assert ("parcel_zoning", {"address": "영천시 금호읍 신월리 1-51"}) in mcp.calls
    assert mcp.calls[-1][1]["region"] == "경상북도 영천시" and _plan(out)["지역"] == "경상북도 영천시"


def test_parcel_failure_still_routes_without_region(sp):
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route, "parcel_zoning": RuntimeError("down")})
    out = asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[0],"주소":"어딘가 1"}')))
    assert _plan(out)["단계"] and _plan(out)["지역"] is None


def test_selection_outside_candidates_gives_empty_plan(sp):
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route})
    out = asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[7]}')))
    assert _plan(out) == {"단계": [], "멈춤": [], "지역": None}
    assert not any(n == "law_route" for n, _ in mcp.calls)


def test_broken_json_gives_empty_plan(sp):
    out = asyncio.run(sp.run_query("질문", mcp=FakeMcp({"law_search": SEARCH, "law_route": _route}),
                                   model=FakeModel("모르겠다")))
    assert _plan(out)["단계"] == [] and out["stopped"] == "answer"


def test_search_failure_gives_empty_plan(sp):
    out = asyncio.run(sp.run_query("질문", mcp=FakeMcp({"law_search": RuntimeError("x"), "law_route": _route}),
                                   model=FakeModel('{"시작":[0]}')))
    assert _plan(out)["단계"] == []


def test_duplicate_steps_are_merged(sp):
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route})
    out = asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[0,1]}')))
    pairs = [(s["law"], s["article"]) for s in _plan(out)["단계"]]
    assert pairs.count(("C법", "제3조")) == 1 and len(pairs) == 3


def test_merged_steps_keep_links_inside_the_plan(sp):
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route})
    steps = _plan(asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[0,1]}'))))["단계"]
    assert all(s["이어진"] is None or 0 <= s["이어진"] < i for i, s in enumerate(steps))


def test_each_search_scope_is_searched_and_merged(sp, monkeypatch):
    monkeypatch.setenv("PLAN_SEARCH_SCOPES", "law,all")
    law_only = {"status": "ok", "facts": {"결과": [
        {"law": "L법", "article": "제9조", "title": "t", "호출인자": {"law": "L법", "article": "제9조"}}]}}
    mcp = FakeMcp({"law_search": lambda a: law_only if a.get("scope") == "law" else SEARCH, "law_route": _route})
    model = FakeModel('{"시작":[0]}')
    asyncio.run(sp.run_query("질문", mcp=mcp, model=model))
    assert [a.get("scope") for n, a in mcp.calls if n == "law_search"] == ["law", "all"]
    assert "0. L법 제9조" in json.dumps(model.seen[0], ensure_ascii=False)


def test_must_steps_are_capped_for_the_whole_plan(sp, monkeypatch):
    monkeypatch.setenv("PLAN_MUST_MAX", "2")
    mcp = FakeMcp({"law_search": SEARCH, "law_route": _route})
    steps = _plan(asyncio.run(sp.run_query("질문", mcp=mcp, model=FakeModel('{"시작":[0,1]}'))))["단계"]
    assert [(s["article"], s["꼭_열기"]) for s in steps] == [("제1조", True), ("제3조", False), ("제2조", True)]   # 시작 조문부터
