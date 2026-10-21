"""`agent.yaml` + 실행 환경 → `AgentCard`.

카드는 **손으로 유지하지 않는다.** 사람이 쓰는 것은 도메인 부분(`agent.yaml`)뿐이고,
주소·확장 선언처럼 실행 환경이 아는 것은 런타임이 채운다. 그래야 선언과 구현이
갈라지지 않는다(SPEC §5.3).

이 파일의 핵심은 `hints` 를 확장으로 옮기는 것이다. `AgentSkill` 에는 부작용을 밝힐
자리가 없어서(규격 `a2a.proto:436`), 그냥 두면 호출자가 이 에이전트를 '쓰기'로
취급해 **병렬 실행에서 조용히 빠진다.** 오류도 로그도 안 난다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .extensions import PROGRESS, TOOL_HINTS, USAGE
from .types import (
    HTTP_JSON,
    PROTOCOL_VERSION,
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
)

#: `agent.yaml` 의 skills[] 에서 카드로 그대로 가는 필드.
_SKILL_FIELDS = ("id", "name", "description", "tags", "examples",
                 "input_modes", "output_modes")
#: 실행 힌트의 키. 없는 것은 보수적으로(= 부작용 있는 쪽으로) 채운다.
_HINT_DEFAULTS = {"readOnly": False, "destructive": False, "idempotent": False}


class CardError(Exception):
    """`agent.yaml` 이 카드가 될 수 없을 때. 기동 시점에 죽는 편이 낫다."""


def _require(spec: dict[str, Any], key: str, where: str) -> Any:
    value = spec.get(key)
    if value in (None, "", [], {}):
        raise CardError(f"{where}: '{key}' 가 없다 — 카드 필수 필드다.")
    return value


def _skill(raw: Any, index: int) -> tuple[AgentSkill, dict[str, bool]]:
    """스킬 하나와 그 실행 힌트. 힌트는 카드의 다른 자리로 간다."""
    where = f"skills[{index}]"
    if not isinstance(raw, dict):
        raise CardError(f"{where}: 객체가 아니다.")
    for key in ("id", "name", "description", "tags"):
        _require(raw, key, where)

    fields = {k: raw[k] for k in _SKILL_FIELDS if k in raw}
    skill = AgentSkill(**fields)

    hints = raw.get("hints") or {}
    if not isinstance(hints, dict):
        raise CardError(f"{where}.hints: 객체가 아니다.")
    unknown = set(hints) - set(_HINT_DEFAULTS)
    if unknown:
        raise CardError(f"{where}.hints: 모르는 키 {sorted(unknown)} "
                        f"— 쓸 수 있는 것은 {sorted(_HINT_DEFAULTS)} 다.")
    return skill, {**_HINT_DEFAULTS, **{k: bool(v) for k, v in hints.items()}}


def _extensions(hints: dict[str, dict[str, bool]]) -> list[AgentExtension]:
    """공표된 URI 로 확장을 선언한다. 이 값들은 설정이 아니다 — `extensions.py` 참고."""
    return [
        AgentExtension(
            uri=TOOL_HINTS, required=False,
            description="스킬별 실행 힌트. 호출자의 스케줄러가 병렬 가능 여부를 판단한다.",
            params={"skills": hints},
        ),
        AgentExtension(
            uri=PROGRESS, required=False,
            description="Task.metadata 에 진행률 {fraction, message} 를 싣는다.",
        ),
        AgentExtension(
            uri=USAGE, required=False,
            description="Task.metadata 에 토큰 사용량 {tokensIn, tokensOut} 을 싣는다.",
        ),
    ]


def build_card(spec: dict[str, Any], *, url: str,
               streaming: bool = True, push_notifications: bool = True) -> AgentCard:
    """`agent.yaml` 을 읽어 카드를 만든다.

    `streaming`/`push_notifications` 는 **런타임이 실제로 구현했는지**를 넘긴다.
    선언과 구현이 갈라지지 않도록 사람이 쓰는 파일에서 받지 않는다(SPEC §5.3).
    """
    if not isinstance(spec, dict):
        raise CardError("agent.yaml 이 객체가 아니다.")

    raw_skills = _require(spec, "skills", "agent.yaml")
    if not isinstance(raw_skills, list):
        raise CardError("agent.yaml: 'skills' 가 목록이 아니다.")

    skills, hints = [], {}
    for i, raw in enumerate(raw_skills):
        skill, hint = _skill(raw, i)
        if skill.id in hints:
            raise CardError(f"스킬 id 가 겹친다: {skill.id!r}")
        skills.append(skill)
        hints[skill.id] = hint

    provider = None
    if raw_provider := spec.get("provider"):
        if not isinstance(raw_provider, dict):
            raise CardError("agent.yaml: 'provider' 가 객체가 아니다.")
        for key in ("organization", "url"):
            _require(raw_provider, key, "provider")     # 넣으면 둘 다 필수다
        provider = AgentProvider(**raw_provider)

    return AgentCard(
        name=_require(spec, "name", "agent.yaml"),
        description=_require(spec, "description", "agent.yaml"),
        version=str(_require(spec, "version", "agent.yaml")),
        supported_interfaces=[AgentInterface(
            url=url.rstrip("/"), protocol_binding=HTTP_JSON,
            protocol_version=PROTOCOL_VERSION,
        )],
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=push_notifications,
            extended_agent_card=False,
            extensions=_extensions(hints),
        ),
        default_input_modes=spec.get("defaultInputModes") or ["text/plain"],
        default_output_modes=(spec.get("defaultOutputModes")
                              or ["text/plain", "application/json"]),
        skills=skills,
        provider=provider,
        documentation_url=spec.get("documentationUrl"),
        icon_url=spec.get("iconUrl"),
    )


def load_card(path: Path, *, url: str, **kw) -> AgentCard:
    if not path.exists():
        raise CardError(f"agent.yaml 이 없다: {path}")
    return build_card(yaml.safe_load(path.read_text(encoding="utf-8")) or {},
                      url=url, **kw)


def side_effect_of(card: AgentCard) -> str:
    """이 에이전트를 병렬로 돌려도 되는가. **보수적으로** 집계한다.

    어느 스킬이 쓰일지 부르기 전에 알 수 없으므로, 하나라도 쓰기면 전체를 쓰기로
    본다. 힌트 선언이 아예 없으면 쓰기다 — 모르면 안전한 쪽이다.

    이 함수는 호출자(오케스트레이터) 쪽 로직이지만, 카드를 만드는 코드와 같은
    자리에 두어야 둘이 어긋나지 않는다.
    """
    for ext in (card.capabilities.extensions or []):
        if ext.uri != TOOL_HINTS:
            continue
        hints = (ext.params or {}).get("skills") or {}
        if hints and all(h.get("readOnly") for h in hints.values()):
            return "read"
        return "mutate"
    return "mutate"
