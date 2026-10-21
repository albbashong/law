"""Ref 목록을 UI legal 블록 형식으로 성형하고 facts 예산 안에 맞춘다."""
import json
import re

EXCERPT = 400


def excerpt(text: str, n: int = EXCERPT) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def ref_dict(r, n: int = EXCERPT) -> dict:
    return {"law": r.law, "article": r.article, "title": r.title, "excerpt": excerpt(r.excerpt, n),
            "note": r.note or r.relation, "doc_id": r.doc_id, "path": r.path}


def pack(refs, budget: int, n: int = EXCERPT) -> tuple[list[dict], list[dict]]:
    """앞에서부터 예산 안에 담고, 넘치는 것은 더_볼_연결 로 돌린다."""
    out, more, used = [], [], 2
    for r in refs:
        d = ref_dict(r, n)
        size = len(json.dumps(d, ensure_ascii=False)) + 1
        if used + size > budget:
            more.append({"law": r.law, "article": r.article, "이유": "응답 크기 제한으로 생략"})
            continue
        out.append(d)
        used += size
    return out, more


def size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def fit(facts: dict, limit: int) -> dict:
    """마지막 안전장치: facts 직렬화가 limit 을 넘으면 refs 뒤에서부터 더_볼_연결 로 옮긴다."""
    more = facts.setdefault("더_볼_연결", [])
    while size(facts) > limit and facts.get("refs"):
        r = facts["refs"].pop()
        more.insert(0, {"law": r["law"], "article": r["article"], "이유": "응답 크기 제한으로 생략"})
    while size(facts) > limit and more:
        more.pop()
    return facts
