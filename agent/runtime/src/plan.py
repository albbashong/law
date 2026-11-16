"""검색 계획 — 답하기 전에 검색 계획 에이전트(PLAN_AGENT_URL)에서 '열어 볼 조문 순서'를 받고, 답 점검 때
계획의 꼭 열 조문을 실제로 열었는지 본다.

계획은 A2A 로 받는다: 질문을 text 파트로 보내고, 종결된 태스크의 `facts` 아티팩트에서 PLAN_KEY 값을 읽는다.
받지 못하면(꺼짐·오류·시간 초과·빈 계획) None — 부르는 쪽은 계획 없이 지금처럼 답한다.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)
_transport: httpx.AsyncBaseTransport | None = None   # 시험이 바꿔 끼우는 자리
#: 계획을 싣는 사용자 메시지의 name — 근거 대조에서 이 메시지를 빼려고 표시한다(재개 턴에도 남는다)
PLAN_MESSAGE_NAME = "search_plan"


async def fetch_plan(url: str, question: str, timeout: float) -> dict[str, Any] | None:
    msg = {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": question}]}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=_transport) as client:
            r = await client.post(url.rstrip("/") + "/message:send", json={"message": msg},
                                  headers={"A2A-Version": "1.0"})
            r.raise_for_status()
            task = r.json().get("task") or {}
    except Exception as exc:  # noqa: BLE001 — 계획 실패는 답을 막지 않는다
        logger.warning("plan fetch failed: %s", exc)
        return None
    for a in task.get("artifacts") or []:
        if a.get("name") != "facts":
            continue
        for p in a.get("parts") or []:
            plan = (p.get("data") or {}).get(settings.plan_key) if isinstance(p, dict) else None
            if isinstance(plan, dict) and plan.get("단계"):
                return plan
    return None


def plan_text(plan: dict[str, Any]) -> str:
    """모델에게 보일 계획 — 단계마다 한 줄(설정 문장 틀)."""
    lines = [settings.plan_intro_text] if settings.plan_intro_text else []
    for i, s in enumerate(plan.get("단계") or []):
        lines.append(settings.plan_step_format.format(
            n=i + 1, law=s.get("law", ""), article=s.get("article", ""), relation=s.get("관계", ""),
            must=settings.plan_must_text if s.get("꼭_열기") else ""))
    stops = [f"{m.get('law', '')} {m.get('article', '')} — {m.get('이유', '')}".strip()
             for m in plan.get("멈춤") or [] if isinstance(m, dict)]
    if stops and settings.plan_stop_text:
        lines.append(settings.plan_stop_text.format(stops="; ".join(stops)))
    return "\n".join(lines)


def _bare(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def unopened(plan: dict[str, Any], messages: list[dict[str, Any]], pointers: set[str]) -> list[str]:
    """꼭 열 조문 중, 한 도구 결과(열어 볼 목록 칸 제외)에서 법령명 바로 뒤(PLAN_OPEN_WINDOW 글자 안)에 그 조문 번호가
    나오지 않은 것. 번호는 경계까지 본다(제77조 ≠ 제77조의2). 계획에 이 법령명으로 시작하는 더 긴 이름(시행령 등)이
    있으면 그 이름은 먼저 지운다 — 시행령 결과를 법률을 연 것으로 치지 않게."""
    from loop import _without_keys

    names = {_bare(s.get("law") or "") for s in plan.get("단계") or []}
    bodies = [_bare(_without_keys(str(m.get("content") or ""), pointers)) for m in messages if m.get("role") == "tool"]
    out = []
    for s in plan.get("단계") or []:
        if not s.get("꼭_열기"):
            continue
        law, article = s.get("law") or "", s.get("article") or ""
        m = re.search(settings.answer_cite_pattern, article) if settings.answer_cite_pattern else None
        num, key = _bare(m.group(0) if m else article), _bare(law)
        longer = sorted((n for n in names if n != key and n.startswith(key)), key=len, reverse=True)
        rx = re.compile(re.escape(key) + r".{0,%d}?" % settings.plan_open_window + re.escape(num) + r"(?!\d|의\d)")

        def seen(body: str) -> bool:
            for n in longer:
                body = body.replace(n, "\u0000")
            return bool(rx.search(body))
        if not any(seen(b) for b in bodies):
            label = f"{law} {article}"
            if label not in out:
                out.append(label)
    return out
