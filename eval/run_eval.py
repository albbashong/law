#!/usr/bin/env python3
"""골든셋으로 law_review 에이전트를 평가한다.

  python3 eval/run_eval.py [--agent http://localhost:7016] [--trace logs/law_mcp_trace.jsonl] [--only G1,G2]

에이전트를 A2A 로 부르고, law-mcp 추적 파일(LAW_MCP_TRACE_FILE)에서 그 사이 도구 호출을 읽어 채점한다.
- 재현율: must_refs 의 (법령, 조문 접두) 가 도구가 돌려준 refs 에 있는가
- 환각 후보: 답변에 쓴 "제N조"/"별표 N" 가 refs 에 없는 것 (사람이 확인)
- 근거상태: building_basis 결과가 expect_basis 와 같은가
- 상태: 태스크 최종 상태가 expect_state 와 같은가 (기본 COMPLETED)
- 라운드: 도구 호출 수
- 여러 턴(turns): 같은 contextId 로 잇고, 에이전트가 낸 memory 를 다음 턴 data 파트로 돌려준다(오케스트레이터
  흉내). 채점은 마지막 답 기준, 재현율·환각 후보는 대화 전체의 도구 호출 기준
- 언급률(must_mention): 마지막 답에 든 문구 비율 — 앞 턴의 조건·다른 허가가 이어졌는지
- 시작(expect_prefix): 마지막 답이 이 문구로 시작하는가 — 이야기 형식 등
- --repeat N: 케이스마다 N번 돌려 케이스별 비율(per_case)을 낸다
"""
import argparse
import json
import re
import time
import urllib.request
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def call(base, text, timeout=300, context_id=None, memory=None):
    parts = [{"text": text}] + ([{"data": {"memory": memory}}] if memory else [])
    msg = {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": parts}
    if context_id:
        msg["contextId"] = context_id
    body = {"message": msg, "configuration": {"returnImmediately": True}}
    req = urllib.request.Request(base + "/message:send", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "A2A-Version": "1.0"})
    task = json.loads(urllib.request.urlopen(req, timeout=60).read())["task"]
    end = time.time() + timeout
    while time.time() < end:
        req = urllib.request.Request(f"{base}/tasks/{task['id']}", headers={"A2A-Version": "1.0"})
        t = json.loads(urllib.request.urlopen(req, timeout=60).read())
        st = t["status"]["state"]
        if st in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_INPUT_REQUIRED",
                  "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            return t
        time.sleep(1.5)
    return {"status": {"state": "TIMEOUT"}, "artifacts": []}


def answer_of(task):
    for a in task.get("artifacts") or []:
        if a.get("name") == "answer":
            return a["parts"][0].get("text", "")
    msg = (task.get("status") or {}).get("message") or {}
    return " ".join(p.get("text", "") for p in msg.get("parts", []))


def memory_of(task):
    for a in task.get("artifacts") or []:
        if a.get("name") == "memory":
            return a["parts"][0].get("data")
    return None


def turns_of(case):
    return list(case.get("turns") or [case.get("utterance", "")])


def norm(s):
    return re.sub(r"[\s·ㆍ]", "", s or "")


CITE_RX = r"(제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?)"


def cited(answer):
    return set(re.findall(CITE_RX, answer))


def tokens(text):
    """조문·별표 번호 토큰(정규화)의 집합 — 부분 문자열이 아니라 번호 단위로 비교한다."""
    return {norm(t) for t in re.findall(CITE_RX, text or "")}


def starts_on_boundary(article, prefix):
    """'제76조제1항' 은 '제76조' 로 시작하지만 '제76조의2' 는 아니다."""
    if not article.startswith(prefix):
        return False
    nxt = article[len(prefix):len(prefix) + 1]
    return not (nxt.isdigit() or nxt == "의")


def evaluate(case, task, trace_rows):
    refs = [(l, a) for row in trace_rows for l, a in (row.get("refs") or []) if l and a]
    grounded = set()
    for _, a in refs:
        grounded |= tokens(a)
    got = []
    for m in case.get("must_refs", []):
        hit = any(norm(l) == norm(m["law"]) and starts_on_boundary(norm(a), norm(m["article"])) for l, a in refs)
        got.append({"law": m["law"], "article": m["article"], "hit": hit})
    ans = answer_of(task)
    for row in trace_rows:
        grounded |= tokens(row.get("texts", ""))
    halluc = sorted(c for c in cited(ans) if norm(c) not in grounded)
    basis = [r.get("근거상태") for r in trace_rows if r.get("tool") == "building_basis" and r.get("근거상태")]
    state = task["status"]["state"].replace("TASK_STATE_", "")
    phrases = case.get("must_mention") or []
    missing = [p for p in phrases if norm(p) not in norm(ans)]
    prefix = case.get("expect_prefix")
    return {
        "mention": (len(phrases) - len(missing)) / len(phrases) if phrases else None, "missing": missing,
        "prefix_ok": ans.strip().startswith(prefix) if prefix else None,
        "id": case["id"], "question": "\n".join(turns_of(case)), "state": state, "state_ok": state == case.get("expect_state", "COMPLETED"),
        "recall": sum(g["hit"] for g in got) / len(got) if got else None, "must": got,
        "basis": basis[-1] if basis else None,
        "basis_ok": (basis[-1] == case["expect_basis"]) if case.get("expect_basis") and basis else
                    (None if not case.get("expect_basis") else False),
        "halluc_candidates": halluc, "rounds": len(trace_rows),
        "tools": [r.get("tool") for r in trace_rows], "answer": ans,
    }


def per_case(results):
    """케이스별 반복 결과 → 비율. 값이 없는 지표(None)는 평균에서 뺀다."""
    def avg(xs):
        xs = [float(x) for x in xs if x is not None]
        return round(sum(xs) / len(xs), 3) if xs else None
    out = {}
    for cid in dict.fromkeys(r["id"] for r in results):
        rs = [r for r in results if r["id"] == cid]
        out[cid] = {"runs": len(rs), "prefix_rate": avg(r.get("prefix_ok") for r in rs),
                    "mention_avg": avg(r.get("mention") for r in rs), "recall_avg": avg(r.get("recall") for r in rs),
                    "halluc_rate": avg(bool(r.get("halluc_candidates")) for r in rs),
                    "state_rate": avg(r.get("state_ok") for r in rs)}
    return out


def run_case(agent, case, trace):
    """턴을 차례로 보내고 (마지막 태스크, 대화 전체의 추적 행)을 돌려준다."""
    before = trace.read_text().count("\n") if trace.exists() else 0
    task, context_id, memory = None, None, None
    for text in turns_of(case):
        task = call(agent, text, context_id=context_id, memory=memory)
        context_id = task.get("contextId") or context_id
        memory = memory_of(task) or memory
    rows = [json.loads(x) for x in (trace.read_text().splitlines()[before:] if trace.exists() else [])]
    return task, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="http://localhost:7016")
    ap.add_argument("--trace", default=str(ROOT / "logs" / "law_mcp_trace.jsonl"))
    ap.add_argument("--golden", default=str(ROOT / "eval" / "golden.yaml"))
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    cases = yaml.safe_load(Path(args.golden).read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    trace = Path(args.trace)
    results = []
    for c in [c for c in cases for _ in range(args.repeat)]:
        t0 = time.time()
        task, rows = run_case(args.agent, c, trace)
        r = evaluate(c, task, rows)
        r["seconds"] = round(time.time() - t0, 1)
        results.append(r)
        print(f"{r['id']}: state={r['state']} recall={r['recall']} basis={r['basis']} "
              f"rounds={r['rounds']} halluc={r['halluc_candidates']} prefix={r['prefix_ok']} "
              f"mention={r['mention']} missing={r['missing']} {r['seconds']}s", flush=True)
    out = ROOT / "eval" / "results" / time.strftime("%Y%m%d-%H%M%S.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    rec = [r["recall"] for r in results if r["recall"] is not None]
    print(json.dumps({
        "cases": len(results),
        "recall_avg": round(sum(rec) / len(rec), 3) if rec else None,
        "state_ok": sum(r["state_ok"] for r in results),
        "basis_ok": sum(1 for r in results if r["basis_ok"]),
        "basis_cases": sum(1 for r in results if r["basis_ok"] is not None),
        "halluc_cases": sum(1 for r in results if r["halluc_candidates"]),
        "rounds_avg": round(sum(r["rounds"] for r in results) / len(results), 2),
        "per_case": per_case(results) if args.repeat > 1 else None,
        "file": str(out)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
