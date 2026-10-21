"""vLLM 은 보통 인증 없이 돈다 — VLLM_API_KEY 를 비워 두어도 클라이언트가 키 없음으로 멈추지 않는다."""
import loop
from config import settings


def test_empty_api_key_uses_a_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "vllm_api_key", "")
    client = loop.ModelAdapter()._get_client()
    assert client.api_key
