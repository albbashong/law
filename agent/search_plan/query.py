"""검색 계획 — 질문 하나에 '이 순서로 이 조문들을 열어 보라'는 계획을 만든다(모델 루프 없이 정해진 순서).

1) 검색 도구로 후보 조문을 뽑는다.
2) 모델을 한 번 불러 후보 번호 중 시작 조문을 고르게 한다(후보 밖 번호는 버린다). 같은 호출이 질문의 필지 주소도 옮겨 적는다.
3) 주소가 있으면 필지 도구로 시도·시군구를 정한다(조례까지 내려가려고).
4) 시작 조문마다 경로 도구(law_route)로 열 순서를 받아 이어 붙인다.

어느 단계든 실패하면 빈 계획을 낸다 — 답하는 쪽은 계획 없이 지금처럼 스스로 찾는다.
도구 이름·키·문장은 모두 설정(.env)에서 읽는다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from config import AGENT_DIR

logger = logging.getLogger(__name__)
HERE = Path(os.environ.get("PLAN_AGENT_DIR") or AGENT_DIR)


class PlanSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(HERE / ".env"), extra="ignore")
    plan_key: str = "검색_계획"
    plan_search_tool: str = ""
    plan_search_limit: int = 20
    plan_search_scopes: str = ""        # 검색 도구의 범위 인자 값들(쉼표) — 범위마다 한 번씩 찾아 후보를 합친다. 비우면 한 번
    plan_scope_arg: str = "scope"
    plan_route_tool: str = ""
    plan_parcel_tool: str = ""
    plan_region_keys: str = ""
    plan_select_prompt: str = ""
    plan_pick_key: str = ""
    plan_address_key: str = ""
    plan_candidate_line: str = "{n}. {law} {article} — {title}"
    plan_start_max: int = 3
    plan_steps_max: int = 16
    plan_must_max: int = 5              # 계획 전체의 꼭 열 조문 상한 — 시작에 가까운 것부터 남긴다


def _empty() -> dict[str, Any]:
    return {"단계": [], "멈춤": [], "지역": None}


def _ok(result: Any) -> dict[str, Any] | None:
    return result["facts"] if isinstance(result, dict) and result.get("status") == "ok" and isinstance(result.get("facts"), dict) else None


def candidates(found: dict[str, Any]) -> list[dict[str, str]]:
    """검색 결과 → 후보(호출인자 기준, 같은 조문은 한 번)."""
    out, seen = [], set()
    for row in found.get("결과") or []:
        args = row.get("호출인자") if isinstance(row, dict) else None
        if not (isinstance(args, dict) and args.get("law") and args.get("article")):
            continue
        key = (args["law"], args["article"])
        if key not in seen:
            seen.add(key)
            out.append({"law": args["law"], "article": args["article"], "title": str(row.get("title") or "")})
    return out


def picked(reply: str, n: int, pick_key: str, address_key: str, start_max: int) -> tuple[list[int], str | None]:
    """모델 답 JSON → (후보 범위 안의 번호들, 주소). 읽을 수 없으면 ([], None)."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        data = json.loads(reply[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return [], None
    raw = data.get(pick_key) if isinstance(data, dict) else None
    nums = []
    for x in raw if isinstance(raw, list) else [raw]:
        if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < n and x not in nums:
            nums.append(x)
    address = data.get(address_key) if isinstance(data, dict) else None
    return nums[:start_max], (address.strip() if isinstance(address, str) and address.strip() else None)


def cap_must(plan: dict[str, Any], most: int) -> None:
    """꼭 열기를 most 개까지 — 시작에서 가까운 단계(걸음 수), 같으면 계획 순서로 남기고 나머지는 참고로 돌린다."""
    depth: dict[int, int] = {}
    for i, s in enumerate(plan["단계"]):
        back = s.get("이어진")
        depth[i] = 0 if back is None else depth.get(back, 0) + 1
    must = sorted((i for i, s in enumerate(plan["단계"]) if s.get("꼭_열기")), key=lambda i: (depth[i], i))
    for i in must[most:]:
        plan["단계"][i]["꼭_열기"] = False


def merge(plan: dict[str, Any], route: dict[str, Any], steps_max: int) -> None:
    """한 시작 조문의 경로를 계획 뒤에 잇는다 — 같은 조문은 한 번, '이어진' 번호는 계획 안 번호로 고친다."""
    where = {(s["law"], s["article"]): i for i, s in enumerate(plan["단계"])}
    local: dict[int, int] = {}
    for i, s in enumerate(route.get("단계") or []):
        key = (s.get("law"), s.get("article"))
        if key in where:
            local[i] = where[key]
            continue
        if len(plan["단계"]) >= steps_max:
            break
        back = s.get("이어진")
        if back is not None and back not in local:
            continue
        where[key] = local[i] = len(plan["단계"])
        plan["단계"].append({**s, "이어진": local[back] if back is not None else None})
    plan["멈춤"] += [m for m in route.get("멈춤") or [] if m not in plan["멈춤"]]


async def run_query(query: str, *, mcp: Any = None, model: Any = None, on_progress: Any = None,
                    prior: Any = None, context: Any = None) -> dict[str, Any]:
    from loop import McpAdapter, ModelAdapter

    s = PlanSettings()
    owns_mcp, owns_model = mcp is None, model is None
    mcp, model = mcp or McpAdapter(), model or ModelAdapter()
    usage = {"tokens_in": 0, "tokens_out": 0}
    plan = _empty()
    try:
        plan = await _plan(query, s, mcp, model, usage)
    except Exception as exc:  # noqa: BLE001 — 계획 실패는 답을 막지 않는다
        logger.warning("plan failed: %s", exc)
    finally:
        if owns_mcp and hasattr(mcp, "aclose"):
            await mcp.aclose()
        if owns_model and hasattr(model, "aclose"):
            await model.aclose()
    logger.info("plan: %d steps (%d must), region=%s", len(plan["단계"]),
                sum(bool(x.get("꼭_열기")) for x in plan["단계"]), plan["지역"])
    return {"answer": "", "tool_results": {"search_plan": {"status": "ok", "facts": {s.plan_key: plan}}},
            "stopped": "answer", "usage": usage}


async def _plan(query: str, s: PlanSettings, mcp: Any, model: Any, usage: dict[str, int]) -> dict[str, Any]:
    plan = _empty()
    # 범위별로 찾는다 — 조례 조문이 많아 한 번에 찾으면 위임하는 법령 조문이 후보에서 밀린다(실측 2026-11-16: 건폐율 질문 후보 40개가 모두 조례)
    scopes = [x.strip() for x in s.plan_search_scopes.split(",") if x.strip()] or [None]
    rows: list[Any] = []
    for scope in scopes:
        args = {"query": query, "limit": s.plan_search_limit, **({s.plan_scope_arg: scope} if scope else {})}
        rows += (_ok(await mcp.call(s.plan_search_tool, args)) or {}).get("결과") or []
    cands = candidates({"결과": rows})
    if not cands:
        return plan
    prompt = (HERE / s.plan_select_prompt).read_text(encoding="utf-8")
    listing = "\n".join(s.plan_candidate_line.format(n=i, **c) for i, c in enumerate(cands))
    step = await model.next_step([{"role": "system", "content": prompt},
                                  {"role": "user", "content": f"{query}\n\n{listing}"}], [])
    for k in usage:
        usage[k] += (step.get("usage") or {}).get(k) or 0
    nums, address = picked(step.get("answer") or "", len(cands), s.plan_pick_key, s.plan_address_key, s.plan_start_max)
    if not nums:
        return plan
    if address and s.plan_parcel_tool:
        try:
            parcel = _ok(await mcp.call(s.plan_parcel_tool, {"address": address}))
        except Exception as exc:  # noqa: BLE001 — 지역 없이도 상위 경로는 계산한다
            logger.warning("parcel failed: %s", exc)
            parcel = None
        keys = [k.strip() for k in s.plan_region_keys.split(",") if k.strip()]
        region = " ".join(str(parcel[k]) for k in keys if parcel and parcel.get(k))
        plan["지역"] = region or None
    for i in nums:
        try:
            route = _ok(await mcp.call(s.plan_route_tool, {"law": cands[i]["law"], "article": cands[i]["article"],
                                                           "region": plan["지역"]}))
        except Exception as exc:  # noqa: BLE001 — 그 시작만 건너뛴다
            logger.warning("route failed: %s", exc)
            continue
        if route:
            merge(plan, route, s.plan_steps_max)
    if s.plan_must_max > 0:
        cap_must(plan, s.plan_must_max)
    return plan
