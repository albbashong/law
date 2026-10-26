"""건축 가능 근거 조회 — 용도지역별 건축제한(시행령 별표·조례 별표)과 건폐율·용적률을 한 번에."""
import re
from typing import Annotated

from pydantic import Field

from law_mcp.config import get_settings
from law_mcp.engine.facts import excerpt, fit, pack, size
from law_mcp.engine.follow import Ref, follow, node
from law_mcp.engine.permits import development_permit, land_permits, permits_for_term, road_access
from law_mcp.engine.rules import find_items, header_of, is_partial_item, normalize_zone, section_kind, subtype_cover
from law_mcp.engine.uses import facility_term, match_use, names as use_names
from law_mcp.tools.article import resolve_region
from law_mcp.tools.common import READ_ONLY, result, trace
from law_mcp.tools.parcel import with_regulations

ENF = "국토의 계획 및 이용에 관한 법률 시행령"
EXPLAIN = {
    "시행령_허용": "시행령 별표가 이 용도를 열거합니다.",
    "조례_허용": "시행령이 조례에 맡긴 용도이고, 해당 조례 별표가 열거합니다.",
    "조건부_허용": "이 용도 중 일부 유형·조건만 허용 목록에 있습니다. 조건(열거된 유형)에 해당하는지 확인이 필요합니다.",
    "조례_미포함": "시행령이 조례에 맡긴 용도인데, 해당 조례 별표에는 열거되어 있지 않습니다.",
    "미열거": "시행령·조례 별표에서 이 용도가 열거되지 않았거나 금지 대상입니다.",
    "조건부_제한": "이 용도 중 일부 유형·입지 조건만 금지되어 있습니다. 조건에 해당하는지 확인이 필요합니다.",
    "미확인": "시행령은 조례에 맡겼지만 조례 별표 원문을 확보하지 못했습니다. 조례 원문 확인이 필요합니다.",
}


def _ratio(store, doc_id, title_kw: str, zone: str) -> tuple[str | None, Ref | None]:
    """'용도지역 안에서의 건폐율' 조에서 해당 용도지역 호를 찾는다."""
    rows = store._all("SELECT * FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' AND replace(title,' ','') LIKE ?"
                      " ORDER BY unit_id", (doc_id, f"%용도지역%{title_kw}%"))
    for jo in rows:
        for u in store.units_with_prefix(doc_id, jo["path"]):
            first = re.sub(r"\s+", "", u["text"].split("\n", 1)[0])
            if re.match(rf"^\d+\.{zone}[:：]", first):
                val = re.split(r"[:：]", u["text"], maxsplit=1)[1].strip()
                return excerpt(val, 160), node(store, doc_id, u["path"])
    return None, None


def _ratios(store, plan_id, enf_id, zkey) -> tuple[dict, list[Ref]]:
    """조례의 건폐율·용적률 값과 근거 — 조례에 없으면 시행령 범위로."""
    ratios, refs = {}, []
    for label in ("건폐율", "용적률"):
        val, ref = _ratio(store, plan_id, label, zkey)
        if ref:
            ref.note = f"조례 {label}"
            refs.append(ref)
        evals, eref = _ratio(store, enf_id, label, zkey)
        if eref:
            eref.note = f"시행령 {label} 범위"
            refs.append(eref)
        ratios[label] = val or (f"{evals} (시행령 범위, 조례 값 미확인)" if evals else "미확인")
    return ratios, refs


def _ordinance_source(store, plan_doc_id, zone: str):
    """조례의 용도지역별 건축제한 목록. 별표에 두는 곳과 본문 조문("자연녹지지역 안에서의 건축제한")에
    두는 곳이 있다. 반환: (출처 설명, 항목 목록[{path,label,text,ref_path}]) — 원문이 없으면 (설명, None)."""
    for a in store.annexes(plan_doc_id):
        if zone in re.sub(r"\s+", "", a["title"] or ""):
            full = store.annex(plan_doc_id, a["no"])
            if not full["text"]:
                return f"조례 별표 {a['no']}", None
            items = [{**i, "ref_path": f"별표{a['no']}:{i['path']}", "label": f"별표 {a['no']} {i['label']}"}
                     for i in store.annex_items(full["annex_id"])]
            return f"조례 별표 {a['no']}", items
    for jo in store._all("SELECT * FROM unit WHERE doc_id=? AND path NOT LIKE '%/%'", (plan_doc_id,)):
        t = re.sub(r"\s+", "", jo["title"] or "")
        if t.startswith(zone) and ("건축제한" in t or "건축할수" in t):
            kids = [u for u in store.units_with_prefix(plan_doc_id, jo["path"]) if u["path"] != jo["path"]]
            items = [{"path": u["path"].split("/", 1)[1], "label": u["label"], "text": u["text"],
                      "ref_path": u["path"]} for u in kids]
            return f"조례 {jo['label']}", items
    return None, None


def _item_ref(store, doc_id, no, item, note) -> Ref:  # 시행령 별표 항목
    r = node(store, doc_id, f"별표{no}:{item['path']}")
    r.note = note
    return r


def _unknown_use(store, use) -> dict:
    # pick: 사용자가 아니라 모델이 이 목록에서 골라 다시 부를 것 — 런타임이 되묻기를 한 번 돌려보낸다
    return {"pick": "용도목록", **result("failed", {"용도목록": use_names(store)},
                  f"'{use}'는 건축법 시행령 별표 1 원문에 나오는 용도명·시설명이 아닙니다."
                  " 사용자에게 묻기 전에, 표현의 일반적인 뜻에 가장 가까운 것을 용도목록(호 이름: 목 번호 시설명)에서"
                  " 하나 골라, 그 번호를 '제3호나목'처럼 호·목 번호로(목이 없으면 호 번호만) use 에 넣어 바로 다시"
                  " 호출하세요. 용도목록은 고르기 위한 것일 뿐 지을 수 있는지의 근거가 아니므로, 다시 호출한 결과로 답하세요."
                  " 답변에는 어느 용도로 해석했는지 밝히세요.")}


def _ratios_only(store, region, zone, parcel_facts) -> dict:
    """용도 없이 건폐율·용적률만 물었을 때."""
    if not region or not zone:
        return result("failed", {}, "시군구(region)와 용도지역(zone), 또는 주소(address)가 필요합니다.")
    zkey = normalize_zone(zone)
    if not store.zone(zkey):
        valid = [r["zone"] for r in store._all("SELECT zone FROM zone_map")]
        return result("failed", {"용도지역목록": valid}, f"'{zone}'을 용도지역으로 해석하지 못했습니다.")
    reg, err = resolve_region(store, region)
    if err:
        return err
    ratios, refs = _ratios(store, reg["doc_id"], store.doc_named(ENF)["doc_id"], zkey)
    facts = {"시군구": reg["org"], "용도지역": zkey, **ratios}
    if parcel_facts:
        facts["필지"] = {k: parcel_facts[k] for k in ("주소", "pnu", "법정동", "지목", "면적", "기타규제") if k in parcel_facts}
    packed, over = pack(refs, get_settings().facts_max_chars - size(facts) - 300, 300)
    facts["refs"], facts["더_볼_연결"] = packed, over[:6]
    return result("ok", facts, "용도지역의 건폐율·용적률입니다. 무엇을 지을 수 있는지는 이 결과로 답할 수 없습니다"
                  " — 지으려는 것이 있으면 building_basis 로 찾으세요.")


def run_building_basis(store, use: str, region: str | None = None, zone: str | None = None,
                       land_category: str | None = None, address: str | None = None,
                       parcel_lookup=None) -> dict:
    s = get_settings()
    parcel_facts = None
    if address:
        from law_mcp.tools.parcel import run_parcel_zoning
        pr = run_parcel_zoning(address, lookup=parcel_lookup)
        if pr["status"] != "ok":
            return pr
        parcel_facts = pr["facts"]
        # 시도를 함께 넘긴다 — "중구"·"북구" 같은 동명 자치구, 세종(시군구 없음) 대비
        region = region or f"{parcel_facts.get('시도') or ''} {parcel_facts['시군구'] or ''}".strip()
        land_category = land_category or parcel_facts["지목"]
        # 브이월드 '…지역'에는 도시지역(대분류)·규제지역도 섞여 온다 — 용도지역으로 해석되는 것만
        zones = [z for z in parcel_facts["용도지역"] if store.zone(normalize_zone(z))] or parcel_facts["용도지역"]
        if zones != parcel_facts["용도지역"]:
            parcel_facts = {**parcel_facts, "용도지역": zones,
                            "기타규제": [z for z in parcel_facts["용도지역"] if z not in zones] + parcel_facts.get("기타규제", [])}
        if (use or "").strip() and not match_use(store, use):
            return _unknown_use(store, use)     # 용도지역마다 나누기 전에 — 나누면 용도목록이 묻힌다
        if not zone and len(zones) > 1:
            each = [run_building_basis(store, use, region, z, land_category) for z in zones]
            per_zone = []
            for z, e in zip(zones, each):
                row = {k: e["facts"].get(k) for k in ("용도지역", "근거상태", "조건", "건폐율", "용적률")}
                row["용도지역"] = row["용도지역"] or z
                if e["status"] != "ok":
                    row["상태"] = e["status"]
                    row["안내"] = e["message"][:200]
                per_zone.append(row)
            facts = {"필지": parcel_facts, "용도지역별": per_zone,
                     "refs": [x for e in each for x in e["facts"].get("refs", [])][:12], "더_볼_연결": []}
            worst = "ok" if all(e["status"] == "ok" for e in each) else "partial"
            fit(facts, get_settings().facts_max_chars)
            return result(worst, facts, "필지가 여러 용도지역에 걸쳐 있어 용도지역별로 근거를 정리했습니다."
                          + ("" if worst == "ok" else " 일부 용도지역은 근거를 다 찾지 못했습니다(용도지역별 상태 참고)."))
        zone = zone or (zones[0] if zones else None)
        if not zone:
            return result("failed", {"필지": parcel_facts}, "이 필지의 용도지역을 확인하지 못했습니다. 용도지역을 직접 알려 주세요.")

    if not (use or "").strip():
        return _ratios_only(store, region, zone, parcel_facts)
    uses = match_use(store, use)
    if not uses:
        return _unknown_use(store, use)
    ho, uname = uses[0]["ho"], uses[0]["name"]
    mm = re.search(r"([가-하])목$", uses[0]["label"])
    mok = mm.group(1) if mm else None              # 목 단위로 물었으면(일반음식점 = 제4호 자목) 목 단위로 본다
    term = (uses[0].get("term") or facility_term(uses[0]["text"]) or None) if mok else None
    if not region or not zone:
        return result("failed", {}, "시군구(region)와 용도지역(zone), 또는 주소(address)가 필요합니다.")
    zkey = normalize_zone(zone)
    zm = store.zone(zkey)
    if not zm:
        valid = [r["zone"] for r in store._all("SELECT zone FROM zone_map")]
        return result("failed", {"용도지역목록": valid}, f"'{zone}'을 용도지역으로 해석하지 못했습니다.")
    reg, err = resolve_region(store, region)
    if err:
        return err
    enf = store.doc_named(ENF)
    plan_id = reg["doc_id"]

    # 1) 시행령 별표
    e_annex = store.annex(enf["doc_id"], zm["annex_no"])
    e_items = store.annex_items(e_annex["annex_id"])
    cover = lambda it: subtype_cover(it["text"].split("\n", 1)[0], ho, mok, term)
    e_hits = [it for it in find_items(e_items, ho) if cover(it) is not False]
    refs: list[Ref] = []
    conditions: list[str] = []
    first = lambda it: it["text"].split("\n", 1)[0]
    part = lambda it: (not cover(it)) if cover(it) is not None else is_partial_item(e_items, it, ho)
    opart = lambda it: (not cover(it)) if cover(it) is not None else is_partial_item(o_items, it, ho)
    # 한 용도가 여러 구분(바로 허용 / 조례로 허용 / 금지 / 조례로 금지 가능)에 걸칠 수 있다
    by_kind = {"허용": [], "조례허용": [], "불가": [], "조례제한": []}
    for it in e_hits:
        by_kind[section_kind(header_of(e_items, it))].append(it)
    for it in e_hits[:4]:
        refs.append(_item_ref(store, enf["doc_id"], zm["annex_no"], it, section_kind(header_of(e_items, it))))
        if part(it):
            conditions.append(f"시행령 별표 {zm['annex_no']} {it['label']}: {excerpt(first(it), 160)}")
    head = header_of(e_items, e_hits[0]) if e_hits else (e_items[0]["text"].split("\n")[0] if e_items else "")
    m = re.search(r"(\d+)층\s*이하", head)
    if m:
        conditions.append(f"{m.group(1)}층 이하 (시행령 별표 {zm['annex_no']})")

    # 2) 조례의 건축제한 목록(별표 또는 본문 조문) — 시행령이 조례에 맡긴 구분이 하나라도 있으면 확인
    o_src, o_items = _ordinance_source(store, plan_id, zkey)
    o_hits = [it for it in find_items(o_items, ho) if cover(it) is not False] if o_items else []
    if (by_kind["조례허용"] or by_kind["조례제한"]) and o_items:
        for it in o_hits[:2]:
            r = node(store, plan_id, it["ref_path"])
            r.note = o_src
            refs.insert(0, r)
            if opart(it):
                conditions.append(f"{o_src} {it['label']}: {excerpt(first(it), 160)}")
            conditions += [excerpt(c["text"], 120) for c in o_items
                           if c["path"].startswith(it["path"] + "/") and c["path"].count("/") == it["path"].count("/") + 1][:6]
    status = "ok"
    if zm["mode"] == "can":
        if any(not part(it) for it in by_kind["허용"]):
            verdict = "시행령_허용"
        elif by_kind["조례허용"]:
            if not o_items:
                verdict, status = "미확인", "partial"
            elif any(not opart(h) for h in o_hits):
                verdict = "조례_허용"
            elif o_hits or by_kind["허용"]:
                verdict = "조건부_허용"   # 조례·시행령이 일부 유형만 열거
            else:
                verdict = "조례_미포함"
        elif by_kind["허용"]:
            verdict = "조건부_허용"      # 시행령이 일부 유형만 열거
        else:
            verdict = "미열거"
    else:
        bans = by_kind["불가"]
        if any(not part(it) for it in bans):
            verdict = "미열거"
        else:
            ord_ban = None
            if by_kind["조례제한"]:
                if not o_items:
                    ord_ban, status = "unknown", "partial"
                elif o_hits:
                    ord_ban = "partial" if all(opart(h) for h in o_hits) else "full"
            if ord_ban == "full":
                verdict = "미열거"
            elif bans or ord_ban == "partial":
                verdict = "조건부_제한"
            elif ord_ban == "unknown":
                verdict = "미확인"
            else:
                verdict = "시행령_허용"
    if o_src and not (by_kind["조례허용"] or by_kind["조례제한"]):
        conditions.append(f"{o_src}의 용도지역 건축제한도 함께 확인")

    # 3) 상위 체인: 시행령 별표 → 제71조 → 법 제76조, 조례 항목 → 시행령
    chain = []
    for r in list(refs):
        chain += follow(store, r.doc_id, r.path, plan_id, "up", 2).refs[1:]
    if not refs:   # 열거되지 않음 — 별표 자체를 근거로
        base = node(store, enf["doc_id"], f"별표{zm['annex_no']}")
        base.excerpt = head
        base.note = "건축 금지 목록에 없음" if zm["mode"] == "cannot" else "해당 용도 미열거"
        refs.append(base)
        chain += follow(store, enf["doc_id"], f"별표{zm['annex_no']}", plan_id, "up", 2).refs[1:]

    # 4) 건폐율·용적률
    ratios, rrefs = _ratios(store, plan_id, enf["doc_id"], zkey)
    chain += rrefs

    # 5) 관련 인허가 — 지목·용도를 정의하는 법률에서 제목으로 찾는다 (표를 두지 않는다)
    extra = []
    for r in land_permits(store, land_category):
        r.note = f"지목 {land_category} — {r.title}"
        chain.append(r)
        extra.append(f"{r.title}({r.law} {r.article})")
    for r in permits_for_term(store, [uname, use]):
        r.note = f"{uname} 관련 인허가 — {r.title}"
        chain.append(r)
        extra.append(f"{r.title}({r.law} {r.article})")
    dp = development_permit(store)
    if dp:
        dp.note = "개발행위허가 대상 여부 확인"
        chain.append(dp)
        extra.append(f"{dp.title}({dp.law} {dp.article}) 대상 여부")

    road = (parcel_facts or {}).get("도로접면")
    if road:   # 접도 기준 — 도로접면은 토지특성 분류일 뿐이라 기준 조문과 적용 제외를 함께 본다
        for r in road_access(store):
            r.note = f"대지와 도로 — {r.title} (도로접면: {road}, 토지특성)"
            chain.append(r)
            extra.append(f"{r.title}({r.law} {r.article}) — 도로접면: {road}(토지특성)")

    # 중복 제거 후 예산 안에 담기
    seen, ordered = set(), []
    for r in refs + chain:
        if (r.doc_id, r.path) in seen:
            continue
        seen.add((r.doc_id, r.path))
        ordered.append(r)
    facts = {"시군구": reg["org"], "용도지역": zkey, "건축물용도": f"{uname}(건축법 시행령 별표 1 제{ho}호)",
             "건축물용도_원문": f"건축법 시행령 별표 1 {uses[0]['label']}: {excerpt(uses[0]['text'], 200)}",
             "근거상태": verdict, "근거상태_설명": EXPLAIN[verdict], "조건": conditions[:8], "건폐율": ratios["건폐율"], "용적률": ratios["용적률"],
             "추가검토": extra}
    if parcel_facts:
        facts["필지"] = {k: parcel_facts[k] for k in ("주소", "pnu", "법정동", "지목", "면적", "도로접면", "기타규제")
                         if k in parcel_facts}
    others = [h for h in uses[1:] if h["score"] >= 2 or h["score"] == uses[0]["score"]]
    if others:
        facts["다른_용도_후보"] = [f"제{h['ho']}호 {h['name']}" for h in others[:3]]
    budget = s.facts_max_chars - size(facts) - 300
    packed, over = pack(ordered, budget, 300)
    facts["refs"] = packed
    facts["더_볼_연결"] = over[:6]
    fit(facts, s.facts_max_chars)
    msg = EXPLAIN[verdict]
    msg += " 근거상태는 법령 열거 여부이며 최종 허가 판단이 아닙니다. 조건과 추가검토를 함께 안내하세요."
    if not land_category:
        msg += (" 질문에 토지의 종류(지목)가 나왔다면 지목의 법정 명칭을 land_category 에 넣어 다시 부르세요"
                " — 그 지목의 전용허가 근거가 붙습니다.")
    return result(status, facts, msg)


def run_zone_ratios(store, region: str | None = None, zone: str | None = None, address: str | None = None,
                    parcel_lookup=None) -> dict:
    """지을 것 없이 용도지역의 건폐율·용적률만."""
    return with_regulations(store, run_building_basis(store, "", region, zone, None, address, parcel_lookup))


def call_building_basis(store, use: str, region=None, zone=None, land_category=None, address=None) -> dict:
    if not (use or "").strip():
        return result("failed", {}, "use(지으려는 것)가 비었습니다. 질문에 나온 지으려는 것을 use 에 넣어 다시 부르세요."
                      " 지을 것 없이 건폐율·용적률만 물었다면 zone_ratios 를 부르세요.")
    return with_regulations(store, run_building_basis(store, use, region, zone, land_category, address))


def register(mcp, get_store):
    @mcp.tool(title="건축 가능 근거 조회 — 용도지역별로 지을 수 있는 건축물·건폐율·용적률", annotations=READ_ONLY)
    def building_basis(
        use: Annotated[str, Field(description="지으려는 것 — 건축법 시행령 별표 1의 용도명이나 그 안의 시설명으로 (해석 못하면 용도목록을 돌려준다)")],
        address: Annotated[str | None, Field(description="주소·지번 (주면 용도지역·지목을 직접 확인)")] = None,
        region: Annotated[str | None, Field(description="시군구 (예: 영천시) — address 가 없을 때")] = None,
        zone: Annotated[str | None, Field(description="용도지역 (예: 자연녹지지역) — address 가 없을 때")] = None,
        land_category: Annotated[str | None, Field(description="지목 (예: 전, 답, 임야, 대)")] = None,
    ) -> dict:
        """여기에 공장·단독주택 지을 수 있어? 건폐율·용적률은? — 용도지역별 건축제한의 근거 법령·조례를 찾는다.

        국토계획법 시행령 별표(용도지역별 건축물)와 해당 시군 도시·군계획 조례 별표에서 그 용도를 찾고,
        건폐율·용적률 조문, 상위법, 지목·용도를 정의하는 법률의 허가·승인 조문까지 refs 로 준다.
        주소만 있으면 address 로 넘긴다. 근거상태는 열거 여부이며 허가 판정이 아니다.
        """
        return trace("building_basis", dict(use=use, region=region, zone=zone, land_category=land_category, address=address), call_building_basis(get_store(), use, region, zone, land_category, address))

    @mcp.tool(title="용도지역 건폐율·용적률 — 지을 것 없이 비율만", annotations=READ_ONLY)
    def zone_ratios(
        address: Annotated[str | None, Field(description="주소·지번 (주면 용도지역을 직접 확인)")] = None,
        region: Annotated[str | None, Field(description="시군구 (예: 영천시) — address 가 없을 때")] = None,
        zone: Annotated[str | None, Field(description="용도지역 (예: 자연녹지지역) — address 가 없을 때")] = None,
    ) -> dict:
        """여기 건폐율·용적률 얼마야? — 지으려는 것 없이 용도지역의 건폐율·용적률과 근거 조례·시행령 조문을 찾는다.

        지으려는 것(공장·주택·카페 등)이 질문에 있으면 이 도구가 아니라 building_basis 를 쓴다.
        """
        return trace("zone_ratios", dict(region=region, zone=zone, address=address),
                     run_zone_ratios(get_store(), region, zone, address))
