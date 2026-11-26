"""상태 지침 — 조회 결과가 어떤 상태인지(부른 도구·결과에 든 키·결과 상태값)에 맞는 지침(STATES_DIR/*.txt)을 그 결과
바로 뒤에 붙인다. 질문의 낱말은 보지 않는다. 같은 지침은 한 번만 붙이고, 지침 글은 인용의 근거로 치지 않는다."""
import loop
from config import settings

SITE = "도구: building_basis\n결과키: 근거상태\n---\n근거상태는 판정이 아닙니다."
USES = "결과키: 용도목록\n결과상태: failed\n---\n목록에서 골라 다시 부릅니다."
DEEM = "결과키: 의제_목록, 의제\n---\n제11조의 의제는 원문 그대로 씁니다."
BARE = "---\n조건 없는 지침."
CITE = r"제\s*\d+\s*조"


def _dir(monkeypatch, tmp_path):
    d = tmp_path / "states"
    d.mkdir()
    for name, text in (("입지", SITE), ("용도해석", USES), ("의제", DEEM), ("조건없음", BARE)):
        (d / f"{name}.txt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "states_dir", "states")
    monkeypatch.setattr(settings, "state_heading", "[읽는 법 — {name}]\n{body}")


def test_states_are_read_with_their_conditions(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    s = loop.states()
    assert s["입지"] == (["building_basis"], ["근거상태"], [], "근거상태는 판정이 아닙니다.")
    assert s["용도해석"] == ([], ["용도목록"], ["failed"], "목록에서 골라 다시 부릅니다.")


def test_state_is_decided_by_tool_result_keys_and_status(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    ok = {"status": "ok", "facts": {"근거상태": "조례_허용"}}
    assert loop.states_of("building_basis", ok) == ["입지"]
    assert loop.states_of("zone_ratios", ok) == []                      # 도구가 다르다
    assert loop.states_of("building_basis", {"status": "ok", "facts": {"건폐율": "20"}}) == []    # 키가 없다
    assert loop.states_of("building_basis", {"status": "failed", "facts": {"용도목록": []}}) == ["용도해석"]
    assert loop.states_of("building_basis", {"status": "ok", "facts": {"용도목록": []}}) == []    # 상태값이 다르다
    assert loop.states_of("law_article", {"status": "ok", "facts": {"의제_목록": ["농지전용"]}}) == ["의제"]
    assert loop.states_of("law_article", "글") == []                    # 읽을 수 없는 결과


def test_a_file_without_conditions_never_applies(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    assert "조건없음" not in loop.states_of("building_basis", {"status": "ok", "facts": {"근거상태": "x"}})


def test_no_states_dir_means_no_states(monkeypatch):
    monkeypatch.setattr(settings, "states_dir", "")
    assert loop.states() == {} and loop.states_of("building_basis", {"status": "ok", "facts": {"근거상태": "x"}}) == []


class Mcp:
    def __init__(self, results):
        self.results = results

    async def list_tools(self):
        return [{"name": n, "description": "", "input_schema": {}} for n in ("building_basis", "law_article")]

    async def call(self, name, args):
        return self.results[name]


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


def _call(i, name, **args):
    return {"tools": [{"id": f"c{i}", "name": name, "args": args}]}


def _guides(messages):
    return [m["content"] for m in messages if m.get("name") == loop.STATE_MESSAGE_NAME]


async def test_guide_follows_the_result_that_put_the_run_in_that_state(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    mcp = Mcp({"building_basis": {"status": "ok", "facts": {"근거상태": "조례_허용"}}})
    model = Model([_call(1, "building_basis", use="공장"), {"answer": "답"}])
    await loop.run_query("공장 지을 수 있어?", mcp=mcp, model=model)
    assert _guides(model.seen[0]) == []                                 # 조회 전에는 없다
    assert _guides(model.seen[1]) == ["[읽는 법 — 입지]\n근거상태는 판정이 아닙니다."]
    roles = [m["role"] for m in model.seen[1]]
    assert roles[-2:] == ["tool", "user"]                               # 결과 바로 뒤
    assert "근거상태는 판정이 아닙니다." not in model.seen[1][0]["content"]     # 시스템 프롬프트는 그대로


async def test_same_guide_is_shown_once(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    mcp = Mcp({"building_basis": {"status": "ok", "facts": {"근거상태": "조례_허용"}}})
    model = Model([_call(1, "building_basis", use="공장"), _call(2, "building_basis", use="창고"), {"answer": "답"}])
    await loop.run_query("공장이나 창고 지을 수 있어?", mcp=mcp, model=model)
    assert len(_guides(model.seen[2])) == 1


async def test_a_later_result_adds_its_own_guide(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    mcp = Mcp({"building_basis": {"status": "ok", "facts": {"근거상태": "조례_허용"}},
               "law_article": {"status": "ok", "facts": {"법령": "건축법", "조문": "제11조", "의제_목록": ["농지전용"]}}})
    model = Model([_call(1, "building_basis", use="공장"), _call(2, "law_article", law="건축법", article="제11조"),
                   {"answer": "답"}])
    await loop.run_query("공장 지으려면?", mcp=mcp, model=model)
    assert [g.split("\n")[0] for g in _guides(model.seen[2])] == ["[읽는 법 — 입지]", "[읽는 법 — 의제]"]


async def test_guide_text_is_not_evidence_for_a_citation(monkeypatch, tmp_path):
    """지침에 적힌 조문 번호는 조회한 것이 아니다 — 그것만 보고 인용한 답은 근거 없는 인용으로 되돌린다."""
    _dir(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    mcp = Mcp({"law_article": {"status": "ok", "facts": {"법령": "건축법", "의제_목록": ["농지전용"]}}})
    model = Model([_call(1, "law_article", law="건축법", article="허가"), {"answer": "「건축법」 제11조에 따릅니다."},
                   {"answer": "「건축법」의 허가 조문에 따릅니다."}])
    out = await loop.run_query("건축허가 의제?", mcp=mcp, model=model)
    assert len(model.seen) == 3 and "제11조" not in out["answer"]
