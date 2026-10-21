"""필지 용도지역 확인 (브이월드) — 독립 질의에서 주소만 있을 때."""
from typing import Annotated

from pydantic import Field

from law_mcp.clients.vworld import AddressNotFound, Client, VworldUnavailable
from law_mcp.config import get_settings
from law_mcp.tools.common import READ_ONLY, result, trace

_client: dict = {}


def default_lookup(address: str):
    s = get_settings()
    c = _client.get(s.vworld_api_key)
    if c is None:
        c = _client.setdefault(s.vworld_api_key, Client(s.vworld_api_key, s.vworld_timeout))
    return c.lookup(address)


def _conversion_refs(store, land_category):
    """지목에 따라 필요한 전용허가 조문 — 그 지목을 정의에 담은 법률에서 찾는다."""
    from law_mcp.engine.facts import ref_dict
    from law_mcp.engine.permits import land_permits
    if not store:
        return []
    out = []
    for r in land_permits(store, land_category):
        r.note = f"지목 {land_category} — {r.title}"
        out.append(ref_dict(r, 300))
    return out


def run_parcel_zoning(address: str, lookup=None, store=None) -> dict:
    lookup = lookup or default_lookup
    try:
        p = lookup(address)
    except AddressNotFound:
        return result("failed", {}, f"'{address}' 주소를 찾지 못했습니다. 시군구와 지번을 함께 적어 주세요."
                      " 예: 경상북도 영천시 금호읍 신월리 1-51")
    except VworldUnavailable as e:
        return result("failed", {"오류": str(e)[:200]},
                      "브이월드 필지 조회에 실패했습니다. 시군구와 용도지역을 직접 알려 주면 법령 근거는 찾을 수 있습니다.")
    facts = {"주소": p.address, "pnu": p.pnu, "시도": p.sido, "시군구": p.sigungu, "용도지역": p.zones,
             "기타규제": p.others[:12], "지목": p.land_category, "면적": p.area}
    if p.notes:
        facts["참고"] = p.notes
    refs = _conversion_refs(store, p.land_category)
    if refs:
        facts["refs"] = refs
        facts["필요_인허가"] = [f"{x['note'].split(' — ')[1]}({x['law']} {x['article']})" for x in refs]
    if not p.zones:
        return result("partial", facts, "필지는 찾았지만 용도지역을 확인하지 못했습니다.")
    return result("ok", facts, "용도지역은 토지이용계획 기준입니다. 건축 근거는 building_basis 로 이어서 찾으세요.")


def register(mcp, get_store):
    @mcp.tool(title="필지 용도지역 확인 — 주소로 용도지역·지목·규제 조회", annotations=READ_ONLY)
    def parcel_zoning(
        address: Annotated[str, Field(description="지번·도로명 주소 (예: 경상북도 영천시 금호읍 신월리 1-51)")],
    ) -> dict:
        """이 땅 용도지역이 뭐야? 농지전용 받아야 해? — 주소로 용도지역·지목·면적·규제와 전용허가 근거를 확인한다.

        건축 근거까지 필요하면 building_basis 에 address 를 바로 넘기는 편이 한 번에 끝난다.
        """
        return trace("parcel_zoning", dict(address=address), run_parcel_zoning(address, store=get_store()))
