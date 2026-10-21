"""성형 — `run_query` 의 `tool_results` 를 상위(오케스트레이터)가 읽을 최상위 키로 편다.

에이전트가 도구를 감싸면 안쪽 도구가 낸 값이 결과 안에 묻힌다. 여기서 안 올리면
도형·되묻기 선택지가 상위에 조용히 안 나타난다 — 오류가 나지 않으니 더 위험하다.

**어느 도구의 결과를 되묻기 원천으로 볼지는 에이전트마다 다르다.** 도구 이름·상태값·
식별자 키를 코드에 박지 않고 설정(`CLARIFY_TOOLS`·`CLARIFY_STATUS_VALUES`·
`CANDIDATE_ID_KEYS`)에서 받는다. permit MCP 3종은 `status="needs_disambiguation"` 과
`facts.candidates`(항목마다 `pnu`) 로 되묻기를 밝힌다.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping, Sequence
from typing import Any

from config import AGENT_DIR, settings

LABEL_KEY = "label"
#: 후보 표시를 만들 때 앞 순서로 보는 주소 필드. 없으면 문자열 필드를 이어 붙인다.
_LABEL_FIELDS = ("road_address", "jibun_address")
_NAME_FIELD = "building_name"


def _load_agent_actions():
    """설정 디렉터리에 `actions.py` 가 있으면 그 성형기를 쓴다.

    도구 결과를 지도 명령 같은 **실행 지시**로 옮기는 규칙은 에이전트마다 다르다 —
    지도제어는 좌표를 `map.flyTo` 로 바꾸지만 조회 계열은 옮길 것이 없다. 런타임이
    그 규칙을 알 필요가 없으므로 설정 쪽에 두고 있으면 불러 쓴다.
    """
    path = AGENT_DIR / "actions.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("agent_actions", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "shape_actions", None)


_agent_actions = _load_agent_actions()


def candidate_id(item: Mapping[str, Any], id_keys: Sequence[str]) -> tuple[str, str]:
    """후보가 스스로 밝힌 (식별자 키, 값). 없으면 ("", "")."""
    for key in id_keys:
        value = item.get(key)
        if value not in (None, ""):
            return key, str(value)
    return "", ""


def _label_of(item: Mapping[str, Any]) -> str:
    base = next((str(item[k]) for k in _LABEL_FIELDS if item.get(k)), "")
    if not base:
        base = " ".join(str(v) for k, v in item.items()
                        if isinstance(v, str) and v and k != LABEL_KEY)
    name = item.get(_NAME_FIELD)
    if name and str(name) not in base:
        base = f"{name} ({base})" if base else str(name)
    return base


def project_candidates(raw: Any, id_keys: Sequence[str]) -> list[dict[str, Any]]:
    """후보 목록 → 규격 4.2절 모양: 유일한 식별자 + 서로 구별되는 `label` + 원본 필드.

    표시가 같은 후보가 둘이면 사용자가 골라도 어느 것인지 구별되지 않아 루프가 안
    닫힌다(실측 통과율 0% 의 원인). 겹치면 식별자를 꼬리에 붙인다.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        key, ident = candidate_id(item, id_keys)
        if not ident:
            key, ident = "id", str(index)          # 식별자 없는 후보는 번호를 받는다
        shaped = {k: v for k, v in item.items() if k != LABEL_KEY}
        shaped[key] = ident
        tool_label = item.get(LABEL_KEY)
        shaped[LABEL_KEY] = (
            tool_label if isinstance(tool_label, str) and tool_label else _label_of(item) or ident
        )
        shaped["_id_key"] = key
        out.append(shaped)
    counts: dict[str, int] = {}
    for c in out:
        counts[c[LABEL_KEY]] = counts.get(c[LABEL_KEY], 0) + 1
    for c in out:
        if counts[c[LABEL_KEY]] > 1:
            c[LABEL_KEY] = f"{c[LABEL_KEY]} [{c[c['_id_key']]}]"
        c.pop("_id_key", None)
    return out


def clarify_source(tool_results: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    """되묻기 원천 (도구 이름, 결과) — **가장 최근에 결과를 낸** `CLARIFY_TOOLS` 하나만 본다.

    `loop.run_query` 가 `tool_results` 를 마지막 호출 순서로 유지한다(같은 도구를 다시
    부르면 자리를 뒤로 옮긴다). 애매한 첫 조회 뒤 확정한 두 번째가 오면 확정된 쪽만
    보이므로, 앞선 도구의 묵은 되묻기가 확정된 답을 되묻기로 뒤집지 않는다.
    """
    statuses = set(settings.clarify_status_names())
    last = _last_clarify_tool_result(tool_results)
    if last is None:
        return None
    name, result = last
    if str(result.get("status") or "") in statuses:
        return name, result
    return None


def resolve_view(result: Mapping[str, Any]) -> dict[str, Any]:
    """`resolve` 아티팩트 — 호스트용 원문에서 부피 키(`RESOLVE_DROP_KEYS`)만 뺀 것."""
    drop = set(settings.resolve_drop_names())
    return {k: v for k, v in result.items() if k not in drop}


def _last_clarify_tool_result(
    tool_results: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]] | None:
    """`CLARIFY_TOOLS` 중 가장 최근에 결과를 낸 (이름, 결과). 없으면 None."""
    names = set(settings.clarify_tool_names())
    for name in reversed(list(tool_results)):
        result = tool_results.get(name)
        if name in names and isinstance(result, Mapping):
            return name, result
    return None


def _candidates_of(result: Mapping[str, Any]) -> Any:
    facts = result.get("facts")
    if isinstance(facts, Mapping) and "candidates" in facts:
        return facts.get("candidates")
    return result.get("candidates")


def shape_result(
    answer: str,
    tool_results: Mapping[str, Any],
    usage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """상위가 읽는 최상위 키를 채운다.

    `actions` 는 **모든** 도구 결과에서 모아 이어붙인다. 어느 도구가 지도 명령을
    내는지 이 코드는 모른다 — 도구가 `actions` 키로 밝히면 그대로 올린다. 아무 도구도
    안 실으면 설정 디렉터리의 `actions.py` 가 사실을 명령으로 옮긴다.

    `usage` 가 안 넘어오면 0 으로 채운다 — 자리 자체를 비우지 않는다. 상위가
    비용을 합산할 때 키가 없으면 그 호출만 조용히 0 이 아니라 누락이 된다.
    """
    clarify = clarify_source(tool_results)
    if clarify is not None:
        _, source = clarify
        status = "clarify"
        candidates = project_candidates(_candidates_of(source), settings.candidate_id_names())
        resolve: dict[str, Any] | None = resolve_view(source)
        message = answer or str(source.get("message") or "")
    else:
        status = "ok"
        candidates = []
        last = _last_clarify_tool_result(tool_results)
        resolve = resolve_view(last[1]) if last is not None else None
        message = answer or ""

    actions: list[Any] = []
    for result in tool_results.values():
        if isinstance(result, Mapping):
            actions.extend(result.get("actions") or [])
    if not actions and _agent_actions is not None:
        actions = _agent_actions(tool_results)

    # 도구가 밝힌 사실을 호출 순서대로 겹친다(뒤가 앞을 덮는다). 어떤 키가 무슨 뜻인지 런타임은
    # 모른다 — 부르는 쪽이 다음 호출에 되돌려 줄 수 있도록 자리만 올린다(규격 4.2 `facts`).
    facts: dict[str, Any] = {}
    for result in tool_results.values():
        declared = result.get("facts") if isinstance(result, Mapping) else None
        if isinstance(declared, Mapping):
            facts.update(declared)

    return {
        "message": message,
        "status": status,
        "candidates": candidates,
        "resolve": resolve,
        "actions": actions,
        "facts": facts,
        "tool_results": dict(tool_results),
        "usage": dict(usage) if usage is not None else {"tokens_in": 0, "tokens_out": 0},
    }
