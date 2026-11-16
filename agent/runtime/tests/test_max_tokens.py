"""출력 토큰 상한 — 생각(thinking)이 끝없이 길어지는 작은 모델이 서버를 붙잡지 않게(실측 2026-11-16 qwen3:8b 7분+)."""
from test_required_tool import MSGS, TOOL, Client, _call, _text

import loop
from config import settings


async def test_max_tokens_is_sent_when_set(monkeypatch):
    monkeypatch.setattr(settings, "vllm_max_tokens", 2048)
    client = Client([_text("답")])
    await loop.ModelAdapter(client=client).next_step(MSGS, [])
    assert client.sent[0]["max_tokens"] == 2048


async def test_max_tokens_is_left_out_by_default(monkeypatch):
    monkeypatch.setattr(settings, "vllm_max_tokens", 0)
    client = Client([_call()])
    await loop.ModelAdapter(client=client).next_step(MSGS, [TOOL])
    assert "max_tokens" not in client.sent[0]
