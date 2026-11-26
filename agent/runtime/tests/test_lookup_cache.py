"""같은 질문 다시 묻기 — 검사를 통과한 턴의 (정리된 질문 → 조회 목록)을 잠깐 들고 있다가, 새 대화에서 같은 질문이 오면
그 조회를 첫 라운드에 다시 실행해 원문을 놓는다. 답을 재사용하지 않고 원문을 다시 받는다(법령이 바뀌어도 최신 원문).
실측 2026-11-19 pg: 같은 혈중알코올농도 질문 5번이 매번 다른 경로(없는 운전면허법·제44조·제148조의2)로 찾았다."""
import loop
import lookup_cache
from config import settings

CITE = r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
CALL = {"tool": "law", "args": {"law": "도로교통법", "article": "제148조의2"}}


class Mcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return [{"name": "law", "description": "", "input_schema": {}}]

    async def call(self, name, args):
        self.calls.append((name, dict(args)))
        return {"status": "ok", "facts": {"refs": [{"article": "제148조의2"}]}}


class Model:
    def __init__(self, script):
        self.script, self.seen, self.tools = list(script), [], []

    async def next_step(self, messages, tools, require=False):
        self.seen.append([dict(m) for m in messages])
        self.tools.append([t["name"] for t in tools])
        return self.script.pop(0)


RESTATE = {"answer": '{"질문": "혈중알코올농도 0.03% 운전 처벌", "짐작": "", "이어짐": false}'}


def _on(monkeypatch, tmp_path, ttl=3600):
    (tmp_path / "restate.txt").write_text("정리하세요.", encoding="utf-8")
    monkeypatch.setattr(loop, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(settings, "restate_prompt", "restate.txt")
    monkeypatch.setattr(settings, "answer_cite_pattern", CITE)
    monkeypatch.setattr(settings, "lookup_cache_ttl_sec", ttl)
    monkeypatch.setattr(settings, "lookup_cache_file", "")
    lookup_cache.clear()


async def _first(mcp, answer="도로교통법 제148조의2 에 따라 처벌됩니다."):
    # 확인 안 된 인용은 한 번 되돌려진다 — 같은 답을 한 번 더 낸다
    model = Model([RESTATE, {"tool": CALL["tool"], "args": CALL["args"]}, {"answer": answer}, {"answer": answer}])
    return await loop.run_query("혈중알코올농도 0.03% 운전하면 처벌돼?", mcp=mcp, model=model)


async def test_same_question_replays_the_lookups_of_a_checked_turn(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    await _first(Mcp())
    mcp = Mcp()
    model = Model([RESTATE, {"answer": "도로교통법 제148조의2 에 따라 처벌됩니다."}])
    raw = await loop.run_query("혈중알코올농도 0.03%면 운전 처벌받나요", mcp=mcp, model=model)
    assert mcp.calls == [(CALL["tool"], CALL["args"])]          # 모델이 찾기 전에 런타임이 다시 실행
    assert any(m.get("role") == "tool" and "제148조의2" in m["content"] for m in model.seen[1])
    assert raw["stopped"] == "answer" and raw["ungrounded"] == []


async def test_answer_resting_partly_on_the_lookups_is_kept(monkeypatch, tmp_path):
    # 작은 모델은 계획에만 있던 조문까지 인용해 검사를 다 통과하는 일이 드물다(실측 2026-11-19 pg) — 조회한 원문이
    # 받친 인용이 하나라도 있으면 그 조회는 다음에 다시 쓸 근거다
    _on(monkeypatch, tmp_path)
    raw = await _first(Mcp(), answer="도로교통법 제148조의2, 제44조 에 따라 처벌됩니다.")
    assert raw["ungrounded"] == ["제44조"]
    assert lookup_cache.get("혈중알코올농도 0.03% 운전 처벌") == [CALL]


async def test_unverified_answer_is_not_kept(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    raw = await _first(Mcp(), answer="제44조 에 따라 처벌됩니다.")
    assert raw["ungrounded"]
    assert lookup_cache.get("혈중알코올농도 0.03% 운전 처벌") is None


async def test_off_when_ttl_is_zero(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path, ttl=0)
    await _first(Mcp())
    assert lookup_cache.get("혈중알코올농도 0.03% 운전 처벌") is None


def test_key_ignores_spacing_and_punctuation(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    lookup_cache.put("혈중알코올농도 0.03% 운전 처벌?", [CALL])
    assert lookup_cache.get("혈중알코올농도0.03% 운전처벌") == [CALL]


def test_expired_entries_are_dropped(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    lookup_cache.put("질문", [CALL], now=0)
    assert lookup_cache.get("질문", now=settings.lookup_cache_ttl_sec + 1) is None


def test_entries_survive_a_restart_through_the_file(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "lookup_cache_file", str(tmp_path / "cache.json"))
    lookup_cache.put("질문", [CALL])
    lookup_cache.clear(memory_only=True)
    assert lookup_cache.get("질문") == [CALL]


def test_file_name_can_carry_the_agent_name(monkeypatch, tmp_path):
    _on(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "agent_name", "pg")
    monkeypatch.setattr(settings, "lookup_cache_file", str(tmp_path / "cache-{agent}.json"))
    lookup_cache.put("질문", [CALL])
    assert (tmp_path / "cache-pg.json").exists()
