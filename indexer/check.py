"""새로 만든 색인을 바꿔 끼우기 전에 보는 검사 — 비어 있거나 앞 색인보다 크게 줄어든 표가 있으면 쓰지 않는다.

빌드는 읽지 못한 문서를 건너뛰고, 기준 조문의 문구가 바뀌면 용도지역 표가 빈 채로도 끝난다. 그런 색인이 운영 DB 를
덮으면 도구가 조용히 틀린 답을 낸다(전수조사 2026-11-20)."""
import sqlite3
from pathlib import Path

# 표 이름 → 세는 SQL. 하나라도 0 이면 색인을 쓸 수 없다.
COUNTS = {
    "문서": "SELECT count(*) FROM doc",
    "조문 단위": "SELECT count(*) FROM unit",
    "별표": "SELECT count(*) FROM annex",
    "별표 항목": "SELECT count(*) FROM annex_item",
    "용도지역 표": "SELECT count(*) FROM zone_map",
    "지역–계획 조례": "SELECT count(DISTINCT doc_id) FROM region_plan",
    "법령–조례 연계": "SELECT count(*) FROM link WHERE kind='조례연계'",
    "인용": "SELECT count(*) FROM link WHERE kind='인용'",
    "법령 체계": "SELECT count(*) FROM link WHERE kind='체계'",
    "용도 분류표": "SELECT count(*) FROM annex a JOIN doc d ON d.doc_id=a.doc_id WHERE d.source='law'"
                 " AND replace(a.title,' ','') LIKE '용도별건축물의종류%'",
}
ORPHANS = "SELECT count(*) FROM annex_item WHERE annex_id NOT IN (SELECT annex_id FROM annex)"


def counts(path: Path) -> dict[str, int]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {k: db.execute(sql).fetchone()[0] for k, sql in COUNTS.items()} | {
            "주인 없는 별표 항목": db.execute(ORPHANS).fetchone()[0]}
    finally:
        db.close()


def problems(new: Path, old: Path | None = None, min_ratio: float = 0.9) -> list[str]:
    """새 색인을 쓰면 안 되는 이유들 — 없으면 빈 목록. old 가 있으면 표마다 min_ratio 아래로 줄었는지도 본다."""
    now = counts(new)
    out = [f"{k}: 0건" for k in COUNTS if now[k] == 0]
    if now["주인 없는 별표 항목"]:
        out.append(f"주인 없는 별표 항목: {now['주인 없는 별표 항목']}건")
    if old and Path(old).exists():
        before = counts(old)
        out += [f"{k}: {before[k]} → {now[k]} (앞 색인의 {min_ratio:.0%} 아래)"
                for k in COUNTS if before[k] and now[k] and now[k] < before[k] * min_ratio]
    return out
