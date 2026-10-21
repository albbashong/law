"""용어 정의 조회 — 법률 제2조(정의)의 호, 또는 본문의 "(이하 "○○"라 한다)"에서 그 용어를 찾는다."""
import difflib
from typing import Annotated

from pydantic import Field

from law_mcp.engine.facts import excerpt
from law_mcp.engine.follow import _cited_by
from law_mcp.engine.permits import definitions
from law_mcp.store.db import nospace
from law_mcp.tools.article import resolve_doc
from law_mcp.tools.common import READ_ONLY, result, trace

MAX_DEFS = 5


def run_law_definition(store, term: str, law: str | None = None) -> dict:
    t = nospace(term)
    if len(t) < 2:
        return result("failed", {}, "용어를 2글자 이상으로 적어 주세요. 예: 용도지역, 개발행위, 농지")
    defs = definitions(store)
    if law:
        doc, err = resolve_doc(store, law)
        if err:
            return err
        defs = [d for d in defs if d["doc_id"] == doc["doc_id"]]
    hits = [d for d in defs if d["term"] == t]
    exact = bool(hits)
    if not hits:
        hits = [d for d in defs if t in d["term"]]
    part = None
    if not hits:   # "개발행위허가" → 그 안에 든 정의 용어 중 가장 긴 것("개발행위")
        inner = sorted({d["term"] for d in defs if len(d["term"]) >= 2 and d["term"] in t}, key=len, reverse=True)
        if inner:
            part = inner[0]
            hits = [d for d in defs if d["term"] == part]
    if not hits:
        close = difflib.get_close_matches(t, list(dict.fromkeys(d["term"] for d in defs)), n=5, cutoff=0.6)
        return result("failed", {"비슷한_용어": close},
                      f"'{term}'을 정의한 법률 조문을 찾지 못했습니다. 비슷한 용어로 다시 찾거나 law_search 로 검색하세요.")
    # 같은 용어를 여러 법률이 정의하면, 많이 인용되는 법률(그 용어의 기본법)부터
    hits.sort(key=lambda d: (d["term"] != t, d["kind"] != "정의", -_cited_by(store, d["doc_id"])))
    rows = []
    for d in hits[:MAX_DEFS]:
        u = store.unit(d["doc_id"], d["path"])
        name = store.doc(d["doc_id"])["name"]
        rows.append({"law": name, "article": u["label"], "용어": d["term"], "text": excerpt(d["text"], 500),
                     "호출인자": {"law": name, "article": u["label"]}})
    facts = {"용어": term, "정의": rows}
    if len(hits) > MAX_DEFS:
        facts["다른_정의_법률수"] = len(hits) - MAX_DEFS
    msg = ("법률의 정의 조문입니다." if exact else
           f"'{term}' 자체의 정의는 없어 그 안의 용어 '{part}'의 정의를 돌려줍니다." if part else
           f"'{term}'을 포함하는 용어의 정의입니다.") + \
        " 두 용어의 차이를 물으면 각각 조회해 비교하고, 지정 절차·효력 등은 law_article·law_search 로 이어서 찾으세요."
    return result("ok", facts, msg)


def register(mcp, get_store):
    @mcp.tool(title="용어 정의 조회 — 법령에서 그 용어를 어떻게 정의하는지", annotations=READ_ONLY)
    def law_definition(
        term: Annotated[str, Field(description="뜻을 찾을 법령 용어 (예: 용도지역, 개발행위, 농지, 공장)")],
        law: Annotated[str | None, Field(description="이 법령의 정의만 (예: 국토계획법)")] = None,
    ) -> dict:
        """용도지역이 뭐야? 용도지역과 용도지구 차이는? — 법률 제2조(정의)에서 그 용어를 정의한 조문을 찾는다.

        같은 용어를 여러 법률이 정의하면 그 용어의 기본법(많이 인용되는 법률)부터 돌려준다.
        각 결과의 호출인자를 law_article 에 넣으면 원문과 연결 근거가 나온다.
        """
        return trace("law_definition", dict(term=term, law=law), run_law_definition(get_store(), term, law))
