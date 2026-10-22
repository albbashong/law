#!/usr/bin/env python3
"""답의 수치를 그 답이 인용한 조문 원문과 대조한다 — 사람이 읽기 전에 틀린 수치를 걸러 낸다.

  mcp/.venv/bin/python eval/audit_numbers.py [eval/results/<파일>.json]   # 생략하면 가장 최근 결과

답의 근거 표(| 법령 | 조문 | …)와 본문의 '「법령」 제N조'에서 인용을 뽑아 lawdb 원문(조문은 하위 호·목까지, 별표는
전문)을 가져오고, agent/runtime/src/figures.py 로 단위별 수치를 값으로 비교한다. 원문에 없는 수치를 적는다.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "agent" / "runtime" / "src"), str(ROOT / "mcp" / "src")]

import figures  # noqa: E402
from law_mcp.engine.resolve import parse_article  # noqa: E402
from law_mcp.tools.article import resolve_doc  # noqa: E402
from law_mcp.server import get_store  # noqa: E402

ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.M)
INLINE = re.compile(r"「([^」]+)」\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?)")
ART = re.compile(r"(별표\s*\d+(?:\s*의\s*\d+)?|제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?(?:\s*제?\s*\d+\s*호)?)")


def cited(answer: str) -> list[tuple[str, str]]:
    out = []
    for law, art in ROW.findall(answer) + INLINE.findall(answer):
        law = re.sub(r"[*`]", "", law).strip()
        m = ART.search(re.sub(r"[*`①-⑳]", "", art))
        if law and m and law not in ("법령", "---", "------") and (law, m.group(1)) not in out:
            out.append((law, m.group(1)))
    return out


def original(store, law: str, art: str) -> str:
    doc, err = resolve_doc(store, law)
    if err or not doc:
        return ""
    path = parse_article(art)
    if not path:
        return ""
    if path.startswith("별표"):
        a = store.annex(doc["doc_id"], path[2:].split(":")[0])
        return (a or {}).get("text") or ""
    top = path.split("/")[0]
    return "\n".join(u["text"] or "" for u in store.units_with_prefix(doc["doc_id"], top))


def main():
    files = sorted((ROOT / "eval" / "results").glob("*.json"))
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else files[-1]
    store = get_store()
    total = flagged = 0
    for r in json.load(open(path)):
        answer = r.get("answer") or ""
        refs = cited(answer)
        source = (r.get("question") or "") + "\n" + "\n".join(original(store, l, a) for l, a in refs)
        bad = figures.unmatched(answer, source) if refs else []
        total += 1
        flagged += bool(bad)
        print(f"{r.get('id')}: 인용 {len(refs)}건, 원문에 없는 수치 {len(bad)}건 {bad[:8]}")
        if not refs:
            print("   (근거 인용을 찾지 못해 대조하지 않음)")
    print(f"\n{total}건 중 {flagged}건에서 원문과 맞지 않는 수치 — {path.name}")


if __name__ == "__main__":
    main()
