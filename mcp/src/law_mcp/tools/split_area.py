"""분할 제한면적 조회 — 건축법의 대지 분할 제한 조문에서 위임을 따라 시행령·조례로 내려가 용도지역의 면적을 찾는다.

조문 번호를 두지 않는다. 법률은 조 제목(법률 용어 "분할 제한")으로, 시행령은 그 조를 인용하며 위임 문구를 받는
같은 계열 법령 조문으로, 조례는 법 조문과 시행령 조문을 함께 인용하는 지역 조례 조문으로 찾는다.
"""
import re
from typing import Annotated

from pydantic import Field

from law_mcp.config import get_settings
from law_mcp.engine.facts import excerpt, fit, pack, size
from law_mcp.engine.follow import DELEGATION_HINT, family, node, rank
from law_mcp.engine.permits import definitions, development_permit
from law_mcp.engine.rules import normalize_zone
from law_mcp.engine.structure import leads
from law_mcp.tools.article import resolve_region
from law_mcp.tools.common import READ_ONLY, result, trace
from law_mcp.tools.parcel import with_regulations

ACT = "건축법"
RULE_TITLE = re.compile(r"분할\s*제한")                        # 조 제목의 법률 용어
RULE_WORD = r"분할"                                            # 토지분할(법률 용어)
NO_SPLIT = re.compile(r"분할(?:하여서는|할\s*수)\s*(?:없|아니)")    # 분할 금지 문구(법률 문체)
ITEM = re.compile(r"^\s*\d+\.\s*(.+?)\s*[:：]\s*(.+?)\s*$", re.S)   # "1. 주거지역: 60제곱미터"


def _jo(path: str) -> str:
    return path.split("/", 1)[0]


def _bare(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _rule_article(store, doc_id) -> str | None:
    for jo in store._all("SELECT * FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id", (doc_id,)):
        if RULE_TITLE.search(jo["title"] or ""):
            return jo["path"]
    return None


def _items(store, doc_id, jo) -> list[dict]:
    """조의 호 가운데 "구분: 면적" 꼴인 것."""
    out = []
    for u in store.units_with_prefix(doc_id, jo):
        m = ITEM.match((u["text"] or "").split("\n", 1)[0])
        if u["path"] != jo and m:
            out.append({"path": u["path"], "label": u["label"], "name": m.group(1), "value": m.group(2)})
    return out


def _citers(store, to_doc, jo, from_docs=None) -> list[tuple[int, str]]:
    """jo 를 인용하는 조(from_docs 가 있으면 그 문서들만, 중복 없이, 저장 순서)."""
    seen = []
    for l in store.links_to_prefix(to_doc, jo):
        key = (l["from_doc"], _jo(l["from_path"] or ""))
        if (from_docs is None or l["from_doc"] in from_docs) and l["from_path"] and key not in seen:
            seen.append(key)
    return seen


def _decree(store, act, jo) -> tuple[int, str] | None:
    """법 조문의 위임을 받는 하위 법령 조 — 위임 문구가 있고 "구분: 면적" 호를 가진 것."""
    lower = lambda d: rank(store.doc(d)["kind"]) > rank(act["kind"]) and family(store.doc(d)["name"]) == family(act["name"])
    for d, p in _citers(store, act["doc_id"], jo):
        u = store.unit(d, p)
        if lower(d) and u and DELEGATION_HINT.search(u["text"] or "") and _items(store, d, p):
            return d, p
    return None


def _ordinance(store, act, jo, decree, region_docs) -> tuple[int, str] | None:
    """법 조문을 인용하고(시행령이 있으면 그것도 함께 인용하는) "구분: 면적" 호를 가진 지역 조례 조."""
    cands = _citers(store, act["doc_id"], jo, region_docs)
    if decree:
        both = set(_citers(store, decree[0], decree[1], region_docs))
        cands = [c for c in cands if c in both] + [c for c in cands if c not in both]
    for d, p in cands:
        if _items(store, d, p):
            return d, p
    return None


def _pick(items, zone, zone_names) -> dict | None:
    """용도지역이 속하는 호 — 호의 구분 이름이 용도지역 이름 안에 있으면 그 호, 없으면 어느 용도지역 이름에도
    들지 않는 구분(앞 호에 해당하지 않는 지역)."""
    z = _bare(zone)
    hit = [i for i in items if _bare(i["name"]) in z]
    if hit:
        return hit[0]
    rest = [i for i in items if not any(_bare(i["name"]) in n for n in zone_names)]
    return rest[-1] if rest else None


def _heading(store, doc_id, path) -> str:
    """항·호·목이 딸린 윗단의 첫머리 — 목 하나만 보면 그 목록이 무엇의 목록(허가 대상인지 예외인지)인지 모른다."""
    return "".join(f"{x} › " for x in leads(store, doc_id, path))


def _criterion(store, r, doc_id, path, text, zone=None) -> dict:
    """토지분할 허가기준 한 항목 — 조문·제목, 그 항목이 딸린 윗단의 문장(딸린_문장), 항목 본문(해당)을 칸으로 나눠
    준다. 한 줄로 이어 주면 목록의 성격(허가 대상/허가 없이 됨)과 단서(…는 제외한다)를 섞어 거꾸로 읽는다(실측 W7)."""
    item = {"조문": f"{r.law} {r.article}", "제목": r.title, "딸린_문장": leads(store, doc_id, path),
            "해당": excerpt(text or "", 160)}
    return {**item, "용도지역": zone} if zone else item


def _zone_lines(units, zone) -> list[dict]:
    """조문의 항·호 가운데 이 용도지역이 속하는 구분(…지역)을 이름으로 드는 것."""
    z = _bare(zone)
    return [u for u in units if any(n in z for n in re.findall(r"[가-힣]+?지역", _bare((u["text"] or "").split("\n", 1)[0])))]


def _land_split_rules(store, land: str) -> list[tuple[int, str]]:
    """지목(답·임야 등)이나 땅의 법령 용어(농지·산지 등)를 정의하는 법률에서 분할을 금지하는 항 [(doc_id, path)].
    지목은 정의 본문에 '지목'과 그 지목이 함께 나오는 용어("농지": 전·답·과수원)로 법률을 찾는다."""
    key = _bare(land)
    tok = re.compile(rf"(?<![가-힣]){re.escape(land.strip())}(?![가-힣])")
    docs = {d["doc_id"] for d in definitions(store) if d["kind"] == "정의"
            and (d["term"] == key or ("지목" in d["text"] and tok.search(d["text"])))}
    out, seen = [], set()
    for doc_id in sorted(docs):
        for u in store._all("SELECT path, text FROM unit WHERE doc_id=? AND path LIKE '%/%' ORDER BY unit_id", (doc_id,)):
            jo = _jo(u["path"])
            if (doc_id, jo) not in seen and NO_SPLIT.search(u["text"] or ""):
                seen.add((doc_id, jo))
                out.append((doc_id, u["path"]))
    return out


def _label(store, doc_id, item) -> str:
    return f"{store.doc(doc_id)['name']} {item['label']}"


def run_split_area(store, region: str | None = None, zone: str | None = None, address: str | None = None,
                   parcel_lookup=None, land_category: str | None = None) -> dict:
    s = get_settings()
    parcel_facts, zones = None, [zone] if zone else []
    if address:
        from law_mcp.tools.parcel import run_parcel_zoning
        pr = run_parcel_zoning(address, lookup=parcel_lookup)
        if pr["status"] != "ok":
            return pr
        parcel_facts = pr["facts"]
        land_category = land_category or parcel_facts.get("지목")
        region = region or f"{parcel_facts.get('시도') or ''} {parcel_facts['시군구'] or ''}".strip()
        if not zone:   # 브이월드 '…지역'에는 대분류·규제지역도 섞여 온다 — 용도지역으로 해석되는 것만
            zones = [z for z in parcel_facts["용도지역"] if store.zone(normalize_zone(z))]
    zones = [normalize_zone(z) for z in zones]
    zone_names = [_bare(r["zone"]) for r in store._all("SELECT zone FROM zone_map")]

    act = store.doc_named(ACT)
    jo = _rule_article(store, act["doc_id"]) if act else None
    if not jo:
        return result("failed", {}, f"{ACT}에서 대지의 분할 제한 조문을 찾지 못했습니다. law_search 로 찾으세요.")
    decree = _decree(store, act, jo)
    reg, err = resolve_region(store, region)
    if err:
        return err
    region_docs = {d["doc_id"] for d in store.ordin_docs_of(reg["doc_id"])} if reg else set()
    ordin = _ordinance(store, act, jo, decree, region_docs) if reg else None

    d_items = _items(store, *decree) if decree else []
    o_items = _items(store, *ordin) if ordin else []
    base = o_items or d_items
    picked = [(z, _pick(base, z, zone_names)) for z in zones] if zones else [(None, i) for i in base]

    rows, refs = [], [node(store, act["doc_id"], jo)]
    for z, it in picked:
        if not it:
            rows.append({"용도지역": z, "분할제한면적": "미확인", "구분": ""})
            continue
        row = {"용도지역": z} if z else {}
        row["구분"] = f"{it['name']}: {it['value']}"
        d_it = _pick(d_items, z or it["name"], zone_names) if d_items else None
        if o_items:
            row["분할제한면적"] = it["value"]
            row["조례"] = _label(store, ordin[0], it)
            refs.append(node(store, ordin[0], it["path"]))
        else:
            row["분할제한면적"] = f"{it['value']} 이상 (시행령 범위 — 조례가 더 크게 정할 수 있음, 조례 값 미확인)"
        if d_it:
            row["시행령"] = f"{_label(store, decree[0], d_it)}: {d_it['value']}"
            refs.append(node(store, decree[0], d_it["path"]))
        rows.append(row if z else {k: v for k, v in row.items() if k != "용도지역"})

    # 건축물이 없는 토지의 분할 — 개발행위허가 법령 계열이 이 법 조문을 끌어 쓰는 기준(시행령 별표 등)과,
    # 그 기준을 정하는 도시·군계획 조례의 분할 조문. 조례의 "영 별표 …" 인용은 연결 자료에 없는 곳이 있어 조 제목도 본다.
    criteria = []
    dp = development_permit(store)
    if dp:
        national = [(l["from_doc"], l["from_path"]) for l in store.links_to_prefix(act["doc_id"], jo)
                    if l["from_path"] and family(store.doc(l["from_doc"])["name"]) == family(dp.law)]
        national = list(dict.fromkeys(national))
        national = [(d, p) for d, p in national   # 조와 그 아래 호·목이 함께 걸리면 아래 것만
                    if not any(d == d2 and p2.startswith(p + "/") for d2, p2 in national)]
        for d, p in national:
            r = node(store, d, p)
            if r and re.search(RULE_WORD, r.title + r.excerpt):
                r.note = "토지분할 허가기준"
                criteria.append(_criterion(store, r, d, p, r.excerpt))
                refs.append(r)
    if reg:
        targets = [(act["doc_id"], jo)] + ([ordin] if ordin else [])
        cited = [c for t in targets for c in _citers(store, t[0], t[1], {reg["doc_id"]})]
        titled = [(reg["doc_id"], u["path"]) for u in store._all(
            "SELECT path, title FROM unit WHERE doc_id=? AND path NOT LIKE '%/%' ORDER BY unit_id", (reg["doc_id"],))
            if re.search(RULE_WORD, u["title"] or "")]
        for d, p in dict.fromkeys(titled + cited):
            r = node(store, d, p)
            if not r or not re.search(RULE_WORD, r.title):
                continue
            r.note = "토지분할 허가기준(조례)"
            lines = [_criterion(store, r, d, p, r.excerpt)]
            for z in zones:   # 첫머리만으로는 어느 호가 이 용도지역의 것인지 안 보인다
                for u in _zone_lines([u for u in store.units_with_prefix(d, p) if u["path"] != p], z):
                    lines.append({**_criterion(store, r, d, u["path"], u["text"], z), "조문": f"{r.law} {u['label']}"})
                    refs.append(node(store, d, u["path"]))
            at = len([c for c in criteria if not c["조문"].startswith(dp.law if dp else "\0")])
            criteria[at:at] = [x for i, x in enumerate(lines) if x not in lines[:i]]
            refs.append(r)
    # 지목을 정하는 법률의 분할 금지 — 농지법의 농업생산기반정비사업 시행 농지 같은 것은 용도지역 기준과 따로 걸린다
    land_rules = []
    for d, p in (_land_split_rules(store, land_category) if land_category else []):
        r = node(store, d, p)
        if not r:
            continue
        r.note = f"지목({land_category})을 정하는 법률의 분할 제한"
        body = " ".join(u["text"] or "" for u in store.units_with_prefix(d, p))
        land_rules.append(f"{r.law} {r.article}({r.title}): {_heading(store, d, p)}{excerpt(body, 400)}")
        refs.append(r)
    seen, ordered = set(), []
    for r in refs:
        if r and (r.doc_id, r.path) not in seen:
            seen.add((r.doc_id, r.path))
            ordered.append(r)

    facts = {"시군구": reg["org"] if reg else None, "적용": f"{ACT} {node(store, act['doc_id'], jo).article}: "
             + excerpt(store.unit(act["doc_id"], jo)["text"], 160), "용도지역별": rows, "토지분할_허가기준": criteria[:6]}
    if land_category:
        facts["지목_분할제한"] = land_rules[:3]
    if parcel_facts:
        facts["필지"] = {k: parcel_facts[k] for k in ("주소", "pnu", "법정동", "지목", "면적", "기타규제") if k in parcel_facts}
    packed, over = pack(ordered, s.facts_max_chars - size(facts) - 300, 300)
    facts["refs"], facts["더_볼_연결"] = packed, over[:6]
    fit(facts, s.facts_max_chars)
    msg = "건축물이 있는 대지의 분할 제한면적입니다(적용 참고)."
    if land_rules:
        msg += " 지목_분할제한은 지목을 정하는 법률의 분할 제한으로, 용도지역 기준과 함께 적용될 수 있습니다(요건을 원문대로 안내)."
    elif land_category:
        msg += f" 지목 '{land_category}'을 정하는 법률에서는 분할을 금지하는 조문을 찾지 못했습니다."
    else:
        msg += " 질문의 땅이 농지·산지처럼 지목이 정해져 있으면 land_category 에 넣어 다시 부르면 그 법률의 분할 제한도 찾습니다."
    if criteria:
        msg += (" 건축물이 없는 토지의 분할은 토지분할_허가기준을 따릅니다. 그 항목이 허가를 받아야 하는 것인지 허가 없이 되는 것인지는 딸린_문장(위에서부터 그 항목이 딸린 윗단의 문장)이 정합니다. 땅에 건축물이 있는지는 이 결과로 알 수 없으니,"
                " 질문에서 정해지지 않으면 건축물이 있는 대지와 없는 토지로 나눠 답하세요.")
    if not reg:
        msg += " 시군구(region)나 주소(address)를 주면 조례가 정한 면적을 찾습니다 — 지금 값은 시행령의 하한입니다."
    elif not ordin:
        msg += " 이 지역 조례에서 분할 제한면적 조문을 찾지 못해 시행령 범위만 보였습니다."
    if address and not zones:
        msg += " 이 필지의 용도지역을 확인하지 못해 모든 구분을 보였습니다."
    status = "ok" if all(r.get("분할제한면적") != "미확인" for r in rows) and rows else "partial"
    return result(status, facts, msg)


def register(mcp, get_store):
    @mcp.tool(title="대지 분할 제한면적 — 용도지역별 최소 분할면적과 근거 조례·시행령", annotations=READ_ONLY)
    def split_area(
        address: Annotated[str | None, Field(description="주소·지번 (주면 용도지역을 직접 확인)")] = None,
        region: Annotated[str | None, Field(description="시군구 (예: 영천시) — address 가 없을 때")] = None,
        zone: Annotated[str | None, Field(description="용도지역 (예: 자연녹지지역) — address 가 없을 때")] = None,
        land_category: Annotated[str | None, Field(description="지목(전·답·임야 등) 또는 땅의 법령 용어(농지·산지) — 질문에 나오면")] = None,
    ) -> dict:
        """여기 최소 분할면적 얼마야? 몇 ㎡까지 쪼갤 수 있어? — 대지 분할 제한면적을 법률→시행령→조례 위임을 따라 찾는다.

        건축물이 있는 대지의 분할 제한면적(조례 값, 없으면 시행령 하한)과, 건축물이 없는 토지를 분할할 때의
        개발행위허가 기준 조례(토지분할_허가기준)를 함께 준다. 지목(또는 농지·산지)을 주면 그 땅을 정하는 법률의
        분할 제한(지목_분할제한)도 준다.
        """
        return trace("split_area", dict(region=region, zone=zone, address=address, land_category=land_category),
                     with_regulations(get_store(), run_split_area(get_store(), region, zone, address,
                                                                  land_category=land_category)))
