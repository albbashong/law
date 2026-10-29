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
        c = _client.setdefault(s.vworld_api_key, Client(s.vworld_api_key, s.vworld_timeout,
                                                        retries=s.vworld_retries, retry_wait=s.vworld_retry_wait))
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


REGULATION_EXCERPT = 160
REGULATION_NOTE = ("규제_근거의 규제는 용도지역 기준과 별도로 그 근거 법령의 행위제한이 함께 적용될 수 있습니다."
                   " 답변에 각 규제를 적고, 결론에 영향을 주는지 근거 조문을 law_article 로 열어 확인하세요."
                   " 행위제한이 있으면 그 원문이 금지·제한하는 행위에 지으려는 것이 드는지로만 판단하고,"
                   " 원문으로 확인되지 않으면 '해당 여부 확인 필요'로 적으세요.")


def with_regulations(store, res: dict) -> dict:
    """필지의 기타규제마다 근거 조문을 붙인다. 근거 법령이 있는 규제 이름은 답에서 빠지지 않게 런타임용 키로도 둔다."""
    from law_mcp.engine.facts import excerpt
    from law_mcp.engine.regulations import base_name, regulation_basis
    facts = res.get("facts") or {}
    p = facts["필지"] if isinstance(facts.get("필지"), dict) else facts
    names = p.get("기타규제") or []
    if not store or not names:
        return res
    rows, named = [], []
    for n in names:
        b = regulation_basis(store, n)
        row = {"규제": n}
        if b:   # 첫 근거는 원문 첫머리도 — 모델이 규제의 내용을 지어내지 않고 원문으로 판단하게
            row["근거"] = [f"{x['law']} {x['article']}({x['title']})" for x in b]
            row["법령"] = list(dict.fromkeys(x["law"] for x in b))
            row["조문"] = [{"law": x["law"], "article": x["article"]} for x in b]
            text = b[0]["text"]
            at = text.find(base_name(n))
            row["원문"] = excerpt(text if at < 0 else "…" + text[max(0, at - 20):], REGULATION_EXCERPT)
            rest = next((x for x in b if x.get("제한")), None)
            if rest:   # 그 구역에서 금지·제한하는 행위 — 정의만 보고 "모든 건축 금지"로 지어내지 않게
                row["행위제한"] = f"{rest['law']} {rest['article']}({rest['title']}): " + excerpt(rest["text"], REGULATION_EXCERPT)
        rows.append(row)
        if b:
            named.append(base_name(n))
    if not named:
        return res
    p["규제_근거"], p["규제_안내"] = rows, REGULATION_NOTE
    key = get_settings().mention_key
    return {**res, key: list(dict.fromkeys([*(res.get(key) or []), *named]))}


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
    if p.road_side:   # 토지특성 조사 분류 — 접한 길이·도로 지정 여부는 담지 않는다
        facts["도로접면"] = p.road_side
    if p.legal_dong:
        facts["법정동"] = p.legal_dong
    if p.notes:
        facts["참고"] = p.notes
    refs = _conversion_refs(store, p.land_category)
    if refs:
        facts["refs"] = refs
        facts["필요_인허가"] = [f"{x['note'].split(' — ')[1]}({x['law']} {x['article']})" for x in refs]
    if not p.zones:
        return result("partial", facts, "필지는 찾았지만 용도지역을 확인하지 못했습니다.")
    return with_regulations(store, result("ok", facts, "용도지역은 토지이용계획 기준입니다. 건축 근거는 building_basis 로 이어서 찾으세요."))


def register(mcp, get_store):
    @mcp.tool(title="필지 용도지역 확인 — 주소로 용도지역·지목·규제 조회", annotations=READ_ONLY)
    def parcel_zoning(
        address: Annotated[str, Field(description="지번·도로명 주소 (예: 경상북도 영천시 금호읍 신월리 1-51)")],
    ) -> dict:
        """이 땅 용도지역이 뭐야? 농지전용 받아야 해? — 주소로 용도지역·지목·면적·규제와 전용허가 근거를 확인한다.

        건축 근거까지 필요하면 building_basis 에 address 를 바로 넘기는 편이 한 번에 끝난다.
        """
        return trace("parcel_zoning", dict(address=address), run_parcel_zoning(address, store=get_store()))
