"""도구 공통: 결과 봉투와 도구 주석."""
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)


def result(status: str, facts: dict, message: str, steps: list | None = None) -> dict:
    return {"status": status, "facts": facts, "message": message, "steps": steps or []}


def candidates_result(kind: str, rows: list[dict], label_key: str, message: str) -> dict:
    cands = [{"id": str(r.get("doc_id")), "label": r[label_key]} for r in rows[:8]]
    return result("needs_disambiguation", {"candidates": cands, "대상": kind}, message)


def trace(tool: str, args: dict, res: dict) -> dict:
    """LAW_MCP_TRACE_FILE 이 설정돼 있으면 도구 호출 한 줄을 JSONL 로 남긴다(관측·평가용)."""
    import json
    import os
    import time
    path = os.environ.get("LAW_MCP_TRACE_FILE")
    if path:
        facts = res.get("facts") or {}
        refs = [[r.get("law"), r.get("article")] for r in facts.get("refs", []) if isinstance(r, dict)]
        if facts.get("법령") and facts.get("조문"):   # law_article 의 시작 조문
            refs.insert(0, [facts["법령"], facts["조문"]])
        for row in facts.get("결과", []) if isinstance(facts.get("결과"), list) else []:
            refs.append([row.get("law"), row.get("article")])   # law_search 결과
        for row in facts.get("정의", []) if isinstance(facts.get("정의"), list) else []:
            refs.append([row.get("law"), row.get("article")])   # law_definition 결과
        groups = [facts] + [z for z in facts.get("용도지역별", []) if isinstance(z, dict)]
        parts = [str(x) for g in groups for k in ("조건", "추가검토", "참고") for x in (g.get(k) or [])]
        parts.append(str(facts.get("원문") or ""))
        parts.append(str(facts.get("건축물용도") or ""))
        parts += [str(r.get("excerpt") or "") for r in facts.get("refs", []) if isinstance(r, dict)]
        parts += [f"{m.get('law')} {m.get('article')}" for m in facts.get("더_볼_연결", []) if isinstance(m, dict)]
        parts += [str(r.get("text") or "") for r in facts.get("정의", []) if isinstance(r, dict)]
        parts += [str(x) for x in facts.get("의제_목록", []) or []]
        texts = " | ".join(p for p in parts if p)

        def body(r: dict, key: str) -> str:   # 딸린 항의 문장을 앞에 붙인 그 조문의 글
            return "\n".join([*(r.get("딸린_문장") or []), str(r.get(key) or "")]).strip()
        ref_texts = []   # 조문마다 도구가 보여 준 원문 — 시험 화면의 근거 색인이 펼쳐 보인다
        if facts.get("법령") and facts.get("조문") and facts.get("원문"):
            ref_texts.append([facts["법령"], facts["조문"], body(facts, "원문")])
        for key, rows in (("excerpt", facts.get("refs")), ("snippet", facts.get("결과")), ("text", facts.get("정의"))):
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, dict) and r.get(key):
                    ref_texts.append([r.get("law"), r.get("article"), body(r, key)])
        row = {"ts": time.time(), "tool": tool, "args": args, "status": res.get("status"),
               "근거상태": facts.get("근거상태"), "refs": refs, "texts": texts, "ref_texts": ref_texts,
               "message": res.get("message")}
        with open(path, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return res
