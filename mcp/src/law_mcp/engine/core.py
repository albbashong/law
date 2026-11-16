"""법의 핵심 조문 — 같은 법의 다른 조문이 가장 많이 인용하는 조문. 낱말·제목을 보지 않고 인용 관계(link)만 쓴다.

건축법이면 건축허가·건축신고·사용승인·착공신고가 위에 오고, 조문 순서로 놓으면 그 법이 정한 절차의 단계가 된다.
절차가 아닌 조문이 섞이는 법도 있으니(국토계획법의 구역 지정) 후보로만 내고 고르는 일은 모델이 원문을 보고 한다."""
from law_mcp.engine.follow import Ref, node


def upper_doc(store, doc_id: int) -> int | None:
    """그 법령이 가장 많이 인용하는 다른 법령 — 시행령이면 위임한 법률."""
    row = store._one("SELECT to_doc, count(*) n FROM link WHERE from_doc=? AND to_doc!=? GROUP BY to_doc"
                     " ORDER BY n DESC LIMIT 1", (doc_id, doc_id))
    return row["to_doc"] if row else None


def core_articles(store, doc_id: int, n: int) -> list[Ref]:
    """같은 법의 다른 조문이 인용하는 수가 많은 조문 n 개를 조문 순서로."""
    if n <= 0:
        return []
    rows = store._all(
        "SELECT u.unit_id, u.path, (SELECT count(DISTINCT CASE WHEN instr(l.from_path, '/') > 0"
        "  THEN substr(l.from_path, 1, instr(l.from_path, '/') - 1) ELSE l.from_path END) FROM link l"
        "  WHERE l.from_doc = u.doc_id AND l.to_doc = u.doc_id AND (l.to_path = u.path OR l.to_path GLOB u.path || '/*')"
        "  AND l.from_path != u.path AND l.from_path NOT GLOB u.path || '/*') AS cited"
        " FROM unit u WHERE u.doc_id=? AND u.path NOT LIKE '%/%' AND u.path NOT LIKE '별표%'", (doc_id,))
    top = sorted((r for r in rows if r["cited"]), key=lambda r: (-r["cited"], r["unit_id"]))[:n]
    return [ref for ref in (node(store, doc_id, r["path"]) for r in sorted(top, key=lambda r: r["unit_id"])) if ref]
