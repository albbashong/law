"""법령·조례 전문 검색 (FTS5 trigram, 공백 제거 색인)."""
import math
import re
from itertools import combinations
from typing import Annotated, Literal

from pydantic import Field

from law_mcp.engine.facts import excerpt
from law_mcp.engine.structure import leads
from law_mcp.store.db import nospace
from law_mcp.tools.article import resolve_region
from law_mcp.tools.common import READ_ONLY, result, trace


def _snippet(text: str, term: str, n: int = 160) -> str:
    flat = re.sub(r"\s+", " ", text or "")
    probe = term[:3]
    idx = flat.replace(" ", "").find(probe)
    if idx < 0:
        return excerpt(flat, n)
    # 공백 제거 위치를 원문 위치로 대략 환산
    pos, seen = 0, 0
    for pos, ch in enumerate(flat):
        if ch != " ":
            if seen == idx:
                break
            seen += 1
    start = max(0, pos - n // 3)
    return ("…" if start else "") + flat[start:start + n] + ("…" if start + n < len(flat) else "")


def _focus_word(text: str, query: str, title: str) -> str:
    """제목이 이미 보여 주는 낱말보다 제목에 없는 검색어 낱말 — 조문의 어느 부분이 질문에 맞는지 보이도록."""
    words = [nospace(t) for t in query.split() if len(nospace(t)) >= 2]
    flat, head = nospace(text or ""), nospace(title or "")
    return next((w for w in sorted(words, key=lambda w: w in head) if w in flat), nospace(query))


def _focus_snippet(text: str, query: str, title: str) -> str:
    """검색어 낱말이 나오는 곳을 발췌한다(_focus_word)."""
    return _snippet(text, _focus_word(text, query, title))


def _chunks(query: str) -> list[str]:
    """띄어 쓴 단어 중 3글자 미만은 이웃과 이어 붙인다 (trigram 색인은 3글자 이상만 찾는다)."""
    out, buf = [], ""
    for w in (nospace(t).replace('"', "") for t in query.split()):
        buf += w
        if len(buf) >= 3:
            out.append(buf)
            buf = ""
    if buf:
        if out:
            out[-1] += buf
        elif len(buf) >= 3:
            out.append(buf)
    return out


def _terms(store, query: str) -> list[tuple[str, str]]:
    """낱말마다 (낱말, 검색식) — 3글자 이상은 그대로, 2글자는 색인에 있는 '낱말+한 글자' 조각들의 OR."""
    out = []
    for w in dict.fromkeys(nospace(t).replace('"', "") for t in query.split()):
        grams = store.index_grams(w) if len(w) >= 2 else []
        if grams:
            out.append((w, f'"{w}"' if len(w) >= 3 else "(" + " OR ".join(f'"{g}"' for g in grams) + ")"))
    return out


def _idf(store, expr: str) -> float:
    return math.log((store.unit_count() + 1) / (store.body_df(expr) + 1))


def _match_exprs(query: str, store=None) -> list[tuple[str, dict | None]]:  # dict: 낱말 → (검색식, 드묾)
    """정확도 높은 순서의 (검색식, 드묾 가중치) 후보: 낱말 AND → 낱말을 덜어 낸 AND(드문 조합부터) → 이어 붙인 덩어리
    AND·OR → 낱말 OR(드문 낱말이 많이 든 조문부터) → 3글자 이상 낱말 OR. 가중치가 있으면 그 순으로 줄 세운다."""
    whole = nospace(query).replace('"', "")
    if len(whole) < 3:
        return []
    exprs = []
    terms = _terms(store, query) if store is not None else []
    if len(terms) > 1 and any(e.startswith("(") for _, e in terms):
        exprs.append(" AND ".join(e for _, e in terms))
    # 모두 든 조문이 없으면 낱말을 덜어 낸다 — 흔한 낱말(과태료·허가)을 뺀 드문 조합부터. 낱말 하나만 남기지는 않는다
    idf = {w: (e, _idf(store, e)) for w, e in terms} if len(terms) > 1 else {}
    if 2 < len(terms) <= 5:
        for k in range(len(terms) - 1, 1, -1):
            for combo in sorted(combinations(terms, k), key=lambda c: -sum(idf[w][1] for w, _ in c)):
                exprs.append(" AND ".join(e for _, e in combo))
    words = [nospace(t).replace('"', "") for t in query.split() if len(nospace(t)) >= 3]
    if len(words) > 1:
        exprs.append(" AND ".join(f'"{t}"' for t in words))
    else:
        exprs.append(f'"{whole}"')
    chunks = _chunks(query)
    if len(chunks) > 1:
        exprs.append(" AND ".join(f'"{c}"' for c in chunks))
        exprs.append(" OR ".join(f'"{c}"' for c in chunks))
    if idf:
        exprs.append((" OR ".join(e for _, e in terms), idf))
    # 짧은 낱말이 붙어 덩어리가 원문에 없으면("수의계약 조건" → "수의계약조건") 3글자 이상 낱말만으로 찾는다
    if words:
        exprs.append(" OR ".join(f'"{t}"' for t in words))
    out = [e if isinstance(e, tuple) else (e, None) for e in exprs]
    return list({e: (e, w) for e, w in out}.values())


def _basis_hint(store, query: str, region: str | None) -> dict | None:
    """질의에 용도지역(zone_map)과 건축물 용도(별표 1 시설명)가 함께 있으면 building_basis 호출을 권한다."""
    from law_mcp.engine.uses import match_use
    q = nospace(query)
    zones = [r["zone"] for r in store._all("SELECT zone FROM zone_map")]
    hit = [z for z in zones if nospace(z) in q]
    if not hit:
        return None
    zone = max(hit, key=len)
    use = None
    toks = query.split()
    # 띄어 쓴 용도명("제2종 근린생활시설")도 알아보도록 이웃 두 낱말을 이은 것부터 본다
    cands = [" ".join(toks[i:i + 2]) for i in range(len(toks) - 1)] + toks
    for tok in cands:
        t = nospace(tok)
        if len(t) < 2:
            continue
        for n in range(len(t), 1, -1):          # 붙은 조사를 떼며 별표 1 이름·시설명과 같은지 본다
            m = match_use(store, t[:n])
            if m and m[0]["score"] >= 3:
                use = tok if n == len(t) else t[:n]
                break
        if use:
            break
    if not use:
        return None
    return {"tool": "building_basis", "args": {"use": use, "zone": zone, "region": region}}


# 낱말 끝에 붙는 조사 — 이것만 떼고 비교한다(더 긴 낱말의 앞부분을 용어로 오인하지 않도록)
PARTICLES = {"이", "가", "은", "는", "을", "를", "의", "과", "와", "에", "로", "으로", "에서", "이란", "란",
             "이랑", "랑", "이라는", "라는", "이야", "야", "에서의", "에서는"}


def _definition_hint(store, query: str) -> dict | None:
    """질의 낱말(붙은 조사는 뗀다)이 법률이 정의한 용어면 law_definition 호출을 권한다.

    법령은 '금품등'·'공개공지등'처럼 '…등'을 붙여 용어를 정의하므로 그 꼴도 같은 용어로 본다. 여러 낱말이
    걸리면 정의한 법령이 적은(더 특정한) 용어를 고른다 — 흔한 '공무원'보다 '금품등'."""
    from law_mcp.engine.permits import definitions
    laws: dict[str, set] = {}
    for d in definitions(store):
        laws.setdefault(d["term"], set()).add(d["doc_id"])
    found = []
    for i, tok in enumerate(query.split()):
        t = nospace(tok)
        for n in range(len(t), 1, -1):
            if n == len(t) or t[n:] in PARTICLES:
                term = next((x for x in (t[:n], t[:n] + "등") if x in laws), None)
                if term:
                    found.append((len(laws[term]), i, term))
                    break
    return {"tool": "law_definition", "args": {"term": min(found)[2]}} if found else None


def _with_hint(r: dict, hint: dict | None) -> dict:
    if hint:
        r["facts"]["먼저_부를_도구"] = hint
        r["message"] = (f"질의에 용도지역({hint['args']['zone']})과 건축물 용도({hint['args']['use']})가 있습니다."
                        " 건축 근거는 이 검색보다 building_basis 로 조회하세요(시군구는 region 에). " + r["message"])
    return r


def run_law_search(store, query: str, scope: str = "all", region: str | None = None, limit: int = 8) -> dict:
    r = _with_hint(_run_law_search(store, query, scope, region, limit), _basis_hint(store, query, region))
    d = None if "먼저_부를_도구" in r["facts"] else _definition_hint(store, query)
    if d:
        r["facts"]["용어_정의"] = d
        r["message"] += f" 용어 '{d['args']['term']}'의 뜻은 law_definition 으로 정의 조문을 바로 찾을 수 있습니다."
    return r


def _hit_rows(store, hits, query):
    rows = []
    for h in hits:
        if h["kind"] == "u":
            u = store._one("SELECT u.doc_id, u.path, u.label, u.title, u.text, d.name AS law FROM unit u"
                           " JOIN doc d ON d.doc_id=u.doc_id WHERE u.unit_id=?", (h["ref"],))
            art, title, text = u["label"], u["title"] or "", u["text"]
            # 발췌한 곳이 호·목이면 그것이 딸린 항의 문장(적용 대상·요건)을 함께 — 호 한 줄만으로 적용하지 않게
            lead = leads(store, u["doc_id"], u["path"], focus=_focus_word(text, query, title))
        else:
            u = store._one("SELECT a.no, a.title, a.text, d.name AS law FROM annex a JOIN doc d ON d.doc_id=a.doc_id"
                           " WHERE a.annex_id=?", (h["ref"],))
            art, title, text = f"별표 {u['no']}", u["title"] or "", u["text"]
            lead = []
        row = {"law": u["law"], "article": art, "title": title, "snippet": _focus_snippet(text, query, title),
               "호출인자": {"law": u["law"], "article": art}}
        if lead:
            row["딸린_문장"] = lead
        rows.append(row)
    return rows


def _title_rows(store, query, doc_ids, source, limit):
    """제목에 드문 검색어가 든 조문·별표 — trigram 이 못 찾는 2글자 법령 용어를 보완한다.

    질의에 법령명이 통째로 들어 있으면(지역 지정 없을 때) 그 법령 안에서 나머지 낱말을 본문·별표까지 찾아 앞에 둔다."""
    from law_mcp.engine.follow import _cited_by
    words = list(dict.fromkeys(nospace(t).replace('"', "") for t in query.split() if len(nospace(t)) >= 2))
    named = [] if doc_ids else store.docs_named_in(nospace(query))
    inside = []
    if named:
        rest = [w for w in words if not any(w in d["name_key"] for d in named)]
        inside = store.search_text_in([d["doc_id"] for d in named], rest, limit) if rest else []
    hits = store.search_titles(words, doc_ids, source, POOL // 25)
    if hits:
        best = hits[0]["score"]
        hits = sorted((h for h in hits if h["score"] >= best * 0.999),
                      key=lambda h: (h["src"] != "law", -_cited_by(store, h["doc_id"])))[:limit]
    return _hit_rows(store, inside, query), _hit_rows(store, hits, query)


def _run_law_search(store, query: str, scope: str = "all", region: str | None = None, limit: int = 8) -> dict:
    exprs = _match_exprs(query, store)
    if not exprs and len(nospace(query)) < 2:
        return result("failed", {}, "검색어가 너무 짧습니다. 예: '자연녹지지역 공장', '도로점용료'")
    doc_ids = None
    if region:
        reg, err = resolve_region(store, region)
        if err:
            return err
        doc_ids = [d["doc_id"] for d in store.ordin_docs_of(reg["doc_id"])]
        scope = "ordin"
    source = None if scope == "all" else scope
    rows, seen = [], set()
    for expr, weights in exprs:             # 정확한 식부터 — 모자라면 넓은 식으로 채운다
        for row in _search(store, expr, query, doc_ids, source, limit, weights):
            if (row["law"], row["article"]) not in seen:
                seen.add((row["law"], row["article"]))
                rows.append(row)
        if len(rows) >= limit:
            break
    # 2글자 법령 용어(식비·선물·전용 등)는 trigram 으로 못 찾으므로 제목에서 찾은 것을 앞에 둔다
    inside, titled_all = _title_rows(store, query, doc_ids, source, limit * 2)
    # 그 2글자 낱말이 실제로 제목에 들었을 때만 — 아니면 흔한 낱말(과태료)만 맞은 제목이 본문에서 찾은 것을 밀어낸다
    shorts = [nospace(t) for t in query.split() if len(nospace(t)) == 2]
    short = any(w in nospace(t["title"]) for w in shorts for t in titled_all)
    rows = _merge(rows, titled_all, inside, short)
    rows = rows[: limit * 2]
    if not rows:
        return result("ok", {"결과": []}, f"'{query}'에 맞는 조문이 없습니다. 다른 표현(공식 용어)으로 다시 검색하세요.")
    message = f"{len(rows)}건. 원문과 상위·하위 근거는 호출인자로 law_article 을 부르세요."
    seen_text = nospace(" ".join(f"{r['law']} {r['title']} {r['snippet']}" for r in rows))
    absent = [w for w in dict.fromkeys(nospace(t).replace('"', "") for t in query.split())
              if len(w) >= 2 and w not in seen_text]
    if absent:
        message += (f" 검색어 중 {', '.join(repr(w) for w in absent)}은(는) 결과 조문에 나오지 않습니다. 법령은 같은 대상을"
                    " 다른 용어로 부를 수 있으니, 결과 조문이 쓰는 용어로 바꿔 다시 검색하세요.")
    return result("ok", {"결과": rows}, message)


def _merge(rows, titled_all, inside, short):
    """본문 검색·제목 검색·지목 법령 안 검색 결과를 합친다.

    제목 결과는 법령마다 PER_LAW 건까지 더하고, 2글자 용어가 있으면(short) 제목 순서를 앞세운다 — 본문 검색이 이미
    찾은 조문도 제목 순서의 제자리에 둔다(중복이라 빼면 법령 조문이 조례 뒤로 밀린다). 지목 법령 안 결과는 맨 앞."""
    key = lambda r: (r["law"], r["article"])   # noqa: E731
    seen = {key(r) for r in rows}
    per_law: dict = {}
    for r in rows:
        per_law[r["law"]] = per_law.get(r["law"], 0) + 1
    titled = []
    for t in titled_all:
        if key(t) in seen:
            titled.append(t)
        elif per_law.get(t["law"], 0) < PER_LAW:
            per_law[t["law"]] = per_law.get(t["law"], 0) + 1
            titled.append(t)
    if short:
        first = {key(t) for t in titled}
        rows = titled + [r for r in rows if key(r) not in first]
    else:
        rows = rows + [t for t in titled if key(t) not in seen]
    named = {key(t) for t in inside}
    return inside + [r for r in rows if key(r) not in named]


POOL = 1000         # 다시 줄 세울 후보 수 (bm25 상위)
PER_LAW = 2         # 한 법령이 결과를 독차지하지 않도록 법령마다 이만큼만


def _article_indeg(store, doc_id, path):
    """그 조문(하위 항·호 포함)을 가리키는 링크 수 — 법령 안에서 제도의 본 조문을 고르는 중심성."""
    top = (path or "").split("/", 1)[0]
    if not top:
        return 0
    return store._one("SELECT count(*) n FROM link WHERE to_doc=? AND (to_path=? OR to_path GLOB ?)",
                      (doc_id, top, top + "/*"))["n"]


def _ranked(store, units, query, weights=None):
    """제목에 검색어 낱말이 든 조문 → 그런 조문이 많은 법령 → 많이 인용되는 법령 → 많이 인용되는 조문 → bm25 순,
    법령마다 PER_LAW 건. weights(낱말→드묾)가 있으면 본문에 든 낱말의 드묾 합을 맨 앞 기준으로 둔다."""
    from law_mcp.engine.follow import _cited_by
    words = [nospace(t) for t in query.split() if len(nospace(t)) >= 2] or [nospace(query)]
    hits = {u["unit_id"]: sum(w in nospace(u["title"] or "") for w in words) for u in units}
    per_doc: dict = {}
    for u in units:
        per_doc[u["doc_id"]] = per_doc.get(u["doc_id"], 0) + (hits[u["unit_id"]] > 0)
    indeg = {u["unit_id"]: _article_indeg(store, u["doc_id"], u["path"]) if hits[u["unit_id"]] else 0 for u in units}
    cover = {u["unit_id"]: round(sum(v for w, (_, v) in (weights or {}).items() if w in nospace(u["text"])), 1)
             for u in units}
    units = sorted(units, key=lambda u: (-cover[u["unit_id"]], -hits[u["unit_id"]], -per_doc[u["doc_id"]],
                                         -_cited_by(store, u["doc_id"]), -indeg[u["unit_id"]], u["score"]))
    out, seen = [], {}
    for u in units:
        if seen.get(u["doc_id"], 0) < PER_LAW:
            seen[u["doc_id"]] = seen.get(u["doc_id"], 0) + 1
            out.append(u)
    return out


def _search(store, expr, query, doc_ids, source, limit, weights=None):
    rows = []
    if weights:     # 낱말마다 따로 모은다 — OR 한 번이면 bm25 상위가 흔한 낱말 조문으로 차서 드문 낱말 조문이 빠진다
        units = list({u["unit_id"]: u for e, _ in weights.values()
                      for u in store.search_units(e, doc_ids, source, POOL // len(weights))}.values())
    else:
        units = store.search_units(expr, doc_ids, source, POOL)
    for u in _ranked(store, units, query, weights)[:limit]:
        row = {"law": u["law"], "article": u["label"], "title": u["title"] or "",
               "snippet": _focus_snippet(u["text"], query, u["title"]),
               "호출인자": {"law": u["law"], "article": u["label"]}}
        lead = leads(store, u["doc_id"], u["path"], focus=_focus_word(u["text"], query, u["title"]))
        if lead:     # 발췌한 곳이 호·목이면 딸린 항의 문장(적용 대상·요건)을 함께
            row["딸린_문장"] = lead
        rows.append(row)
    for a in store.search_annex(expr, doc_ids, source, limit):
        art = f"별표 {a['no']} {a['label']}"
        rows.append({"law": a["law"], "article": art, "title": a["annex_title"] or "",
                     "snippet": _focus_snippet(a["text"], query, a["annex_title"]),
                     "호출인자": {"law": a["law"], "article": art}})
    return rows[: limit * 2]


def register(mcp, get_store):
    @mcp.tool(title="법령·조례 검색 — 키워드로 근거 조문 찾기", annotations=READ_ONLY)
    def law_search(
        query: Annotated[str, Field(description="찾을 표현 (예: 가축사육 제한거리, 도로점용료, 자연녹지지역 공장)")],
        scope: Annotated[Literal["all", "law", "ordin"], Field(description="law=법령, ordin=조례")] = "all",
        region: Annotated[str | None, Field(description="이 시군구 조례만 (예: 영천시)")] = None,
        limit: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> dict:
        """법령·조례·별표를 키워드로 검색한다 — "가축사육 제한거리 조례", "도로점용료 기준", "근거 법령 찾아줘".

        조문 단위·별표 항목 단위로 돌려주며, 각 결과의 호출인자를 law_article 에 넣으면 원문과
        상위·하위 근거가 나온다. 지역(region)을 주면 그 지자체 조례만 찾는다.
        """
        return trace("law_search", dict(query=query, scope=scope, region=region, limit=limit), run_law_search(get_store(), query, scope, region, limit))
