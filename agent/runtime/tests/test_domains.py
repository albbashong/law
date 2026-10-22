"""분야 지침 — 공통 프롬프트에 분야별 지침(DOMAINS_DIR/*.txt)을 덧붙인다. 파일 머리의 '법령:'·'도구:' 가 켜는 조건이고,
모델이 실제로 연 법령(호출 인자의 law)이나 부른 도구로 판단한다. 시행령·시행규칙은 법률 이름으로 시작해 같은 분야다."""
import loop
from config import settings

BUILD = "법령: 건축법, 국토의 계획 및 이용에 관한 법률\n도구: building_basis\n---\n건축 지침입니다."
TAX = "법령: 지방세법\n---\n세금 지침입니다."


def _dir(monkeypatch, tmp_path):
    d = tmp_path / "domains"
    d.mkdir()
    (d / "건축개발.txt").write_text(BUILD, encoding="utf-8")
    (d / "지방세.txt").write_text(TAX, encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "domains_dir", "domains")
    monkeypatch.setattr(settings, "domain_law_keys", "law")


def test_domains_are_read_with_their_triggers(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    d = loop.domains()
    assert d["건축개발"] == (["건축법", "국토의 계획 및 이용에 관한 법률"], ["building_basis"], "건축 지침입니다.")
    assert d["지방세"] == (["지방세법"], [], "세금 지침입니다.")


def test_domains_switch_on_by_opened_law_or_called_tool(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    assert loop.active_domains([{"tool": "law_article", "args": {"law": "지방세법 시행령", "article": "제1조"}}]) == ["지방세"]
    assert loop.active_domains([{"tool": "building_basis", "args": {"use": "공장"}}]) == ["건축개발"]
    assert loop.active_domains([{"tool": "law_article", "args": {"law": "건축물관리법"}}]) == []
    assert loop.active_domains([{"tool": "law_search", "args": {"query": "지방세법 세율"}}]) == []   # 검색어는 보지 않는다


def test_no_domains_dir_means_no_domains(monkeypatch):
    monkeypatch.setattr(settings, "domains_dir", "")
    assert loop.domains() == {} and loop.active_domains([{"tool": "building_basis", "args": {}}]) == []


class Mcp:
    async def list_tools(self):
        return [{"name": "law_article", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        return {"status": "ok", "facts": {"원문": "세율"}}


class Model:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def next_step(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


async def test_domain_guidance_joins_the_system_prompt_after_the_lookup(monkeypatch, tmp_path):
    _dir(monkeypatch, tmp_path)
    model = Model([{"tool": "law_article", "args": {"law": "지방세법", "article": "제111조"}}, {"answer": "답"}])
    await loop.run_query("재산세율?", mcp=Mcp(), model=model)
    first, second = model.seen[0][0]["content"], model.seen[1][0]["content"]
    assert "세금 지침입니다." not in first
    assert "세금 지침입니다." in second and "건축 지침입니다." not in second
    assert sum(m["role"] == "system" for m in model.seen[1]) == 1
