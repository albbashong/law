"""따라가기 엔진 — 조문 하나에서 위임·인용·조례 연계를 깊이 제한 BFS로 펼친다.

상위(up):   이 조문이 인용하는 상위 법령 조문(조례 "영 제84조" → 시행령 제84조, 시행령 "법 제77조" → 법)
            시행령 별표는 제목의 "(제71조제1항제16호관련)"으로 해당 조문에 붙는다.
하위(down): 이 조문을 인용하는 하위 법령 조문. 조례는 region_doc_id 의 지자체 것만.
            조례 위임 문구가 있는데 지역이 없으면 more 에 "조례 위임 — 지역 필요".
"""
import re
from collections import deque
from dataclasses import dataclass, field

RANK = {"헌법": 0, "법률": 1, "대통령령": 2, "총리령": 3, "조례": 5, "규칙": 6}
ORDIN_DELEGATION = re.compile(r"(도시ㆍ군계획조례|도시·군계획조례|조례)\s*(로|으로|가|에서)\s*정")
ANNEX_REL = re.compile(r"제\s*(\d+)\s*조(?:의\s*(\d+))?(?:\s*제\s*(\d+)\s*항)?(?:\s*제\s*(\d+)\s*호)?\s*(?:관련|관계)")
MAX_NEIGHBORS = 6
REL_PRIORITY = {"근거조문": 0, "조례": 0, "하위": 1, "상위": 1, "의제": 2, "의제됨": 2, "인용": 3}
# 의제 — "…허가를 받으면 다음 각 호의 허가 등을 받은 것으로 본다" (법률 용어)
DEEM = re.compile(r"(받은|한)\s*것으로\s*(본다|보며|보고|보아|봄)")
DEEMED_BY_MAX = 3
# 하위 법령 조문이 상위 위임을 받는 전형 문구: 법 제11조제1항에서 "대통령령으로 정하는 …"이란
DELEGATION_HINT = re.compile(r"(에서|단서에서)\s*[“\"‘'].{0,40}(대통령령|총리령|부령|국토교통부령|[가-힣]+부령)으로\s*정하는")


@dataclass
class Ref:
    doc_id: int
    path: str
    law: str
    article: str
    title: str
    excerpt: str
    note: str
    relation: str


@dataclass
class FollowResult:
    refs: list[Ref] = field(default_factory=list)
    more: list[dict] = field(default_factory=list)
    visited: set = field(default_factory=set)


def rank(kind: str | None) -> int:
    if not kind:
        return 9
    return RANK.get(kind, 4 if kind.endswith("령") else 9)


def node(store, doc_id: int, path: str) -> Ref | None:
    d = store.doc(doc_id)
    if not d:
        return None
    if path.startswith("별표"):
        no, _, item = path[2:].partition(":")
        a = store.annex(doc_id, no)
        if not a:
            return None
        if item:
            it = store.annex_item(doc_id, no, item)
            if not it:
                return None
            return Ref(doc_id, path, d["name"], f"별표 {no} {it['label']}", a["title"] or "",
                       it["text"], "", "")
        return Ref(doc_id, path, d["name"], f"별표 {no}", a["title"] or "", a["text"] or "", "", "")
    u = store.unit(doc_id, path)
    if not u:
        return None
    return Ref(doc_id, path, d["name"], u["label"], u["title"] or "", u["text"] or "", "", "")


def family(name: str) -> str:
    """'건축법 시행령' → '건축법' — 법률·시행령·시행규칙을 한 계열로 본다."""
    return re.sub(r"\s*시행(령|규칙)$", "", name or "")


def _jo(path: str) -> str:
    return path.split("/", 1)[0]


def _related(a: str, b: str) -> bool:
    """두 경로가 조상-자손(또는 같음) 관계인가."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def deems(store, doc_id: int, path: str) -> bool:
    """이 조문(호)이 의제 목록에 속하는가 — 자기 문장이나 상위 항·호의 머리 문장에 의제 문구가 있다.

    조 단위 텍스트는 하위 항을 이어 붙인 것이라 보지 않는다(다른 항의 의제 문구에 속지 않도록).
    """
    if path.startswith("별표"):
        return False
    u = store.unit(doc_id, path)
    kids = store.children(doc_id, path) if u else []
    if u and "/" in path and not kids and DEEM.search(u["text"] or ""):
        return True     # 하위 호가 있으면 이 문장의 인용은 의제 목록이 아니라 머리 문장의 부수 인용이다
    segs = path.split("/")
    for i in range(len(segs) - 1, 1, -1):
        a = store.unit(doc_id, "/".join(segs[:i]))
        if a and DEEM.search((a["text"] or "").split("\n", 1)[0]):
            return True
    if "/" not in path and u and not kids:
        return bool(DEEM.search(u["text"] or ""))
    return False


_indeg: dict = {}


def _cited_by(store, doc_id: int) -> int:
    """그 법령을 인용하는 문서 수 — 일반법(건축법)이 개별 사업 특례법보다 앞서도록 쓰는 중심성."""
    key = (id(store), doc_id)
    if key not in _indeg:
        _indeg[key] = store._one("SELECT count(DISTINCT from_doc) n FROM link WHERE to_doc=?", (doc_id,))["n"]
    return _indeg[key]


def natural(path: str) -> list:
    """'제11조/5/10' 이 '제11조/5/3' 뒤에 오도록 숫자는 숫자로 비교한다."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", path or "") if t]


def deemed_items(store, doc_id: int, path: str) -> list[str]:
    """의제 조항(예: 건축법 제11조제5항)의 각 호가 가리키는 허가 등 — '제3호 국토의 계획 및 이용에 관한 법률 제56조'."""
    out = []
    links = sorted(store.links_from_prefix(doc_id, path), key=lambda l: natural(l["from_path"]))
    for l in links:
        same = l["to_doc"] == doc_id and _related(l["to_path"] or "", path)
        if l["kind"] != "인용" or same or not l["to_path"] or not deems(store, doc_id, l["from_path"]):
            continue
        u = store.unit(doc_id, l["from_path"])
        label = (u["label"] if u else l["from_path"]).replace(store.unit(doc_id, path)["label"], "") or u["label"]
        to = node(store, l["to_doc"], l["to_path"])
        if to:
            item = f"{label} {to.law} {to.article}"
            if item not in out:
                out.append(item)
    return out


def _deem_note(store, doc_id: int, path: str, direction: str) -> str:
    d = store.doc(doc_id)
    u = store.unit(doc_id, path)
    where = f"{d['name']} {u['label'] if u else path}"
    if direction == "up":
        return f"의제 — 이 조문의 허가 등을 받으면 이 허가 등을 받은 것으로 봄"
    return f"의제됨 — {where}에 따른 허가 등을 받으면 이 조문의 허가 등을 받은 것으로 봄"


def _up(store, ref: Ref) -> list[tuple[int, str, str]]:
    out = []
    me = store.doc(ref.doc_id)
    if ref.path.startswith("별표"):
        no = ref.path[2:].split(":")[0]
        a = store.annex(ref.doc_id, no) or {}
        m = ANNEX_REL.search(a.get("title") or "")
        if m:
            p = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
            if m.group(3) or m.group(4):
                p += "/" + (m.group(3) or "-")
            if m.group(4):
                p += "/" + m.group(4)
            if not store.unit(ref.doc_id, p):
                p = _jo(p)
            out.append((ref.doc_id, p, "근거조문"))
        links = store.links_from(ref.doc_id, ref.path)
        base, _, item = ref.path.partition(":")
        segs = item.split("/") if item else []
        for i in range(1, len(segs)):   # 상위 항목의 인용도 이 항목의 근거다
            links += store.links_from(ref.doc_id, f"{base}:{'/'.join(segs[:i])}")
    else:
        links = sorted(store.links_from_prefix(ref.doc_id, ref.path), key=lambda l: natural(l["from_path"]))
        segs = ref.path.split("/")
        for i in range(1, len(segs)):   # 조·항 머리 문장의 인용("영 제84조제1항의 규정에 의하여")
            links += store.links_from(ref.doc_id, "/".join(segs[:i]))
    # lnkLsOrdJo(조례연계)는 잡음이 있다 — 이 조문(과 머리 문장)이 실제로 인용한 법령으로만 올라간다
    cited = {l["to_doc"] for l in links if l["kind"] == "인용"}
    for l in links:
        if not l["to_path"] or (l["to_doc"] == ref.doc_id and _related(l["to_path"], ref.path)):
            continue
        if l["kind"] == "조례연계" and l["to_doc"] not in cited:
            continue
        target = store.doc(l["to_doc"])
        rel = "상위" if rank(target["kind"]) < rank(me["kind"]) else "인용"
        if l["to_doc"] == ref.doc_id and l["to_path"].startswith("제") and ref.path.startswith("별표"):
            rel = "근거조문"
        if l["kind"] == "인용" and l["to_doc"] != ref.doc_id and l["from_path"] and deems(store, ref.doc_id, l["from_path"]):
            out.append((l["to_doc"], l["to_path"], "의제", _deem_note(store, ref.doc_id, l["from_path"], "up")))
            continue
        out.append((l["to_doc"], l["to_path"], rel))
    return out


def _down(store, ref: Ref, region_doc_id, res: FollowResult) -> list[tuple[int, str, str]]:
    out = []
    me = store.doc(ref.doc_id)
    region_docs = set()
    if region_doc_id:
        region_docs = {d["doc_id"] for d in store.ordin_docs_of(region_doc_id)}
    deemed = {}
    for l in store.links_to_prefix(ref.doc_id, _jo(ref.path)):
        if l["from_doc"] == ref.doc_id or not l["from_path"] or not _related(l["to_path"], ref.path):
            continue
        src = store.doc(l["from_doc"])
        if src["source"] == "law" and l["kind"] == "인용" and deems(store, l["from_doc"], l["from_path"]):
            paths = deemed.setdefault(l["from_doc"], [])
            if l["from_path"] not in paths:
                paths.append(l["from_path"])     # 한 법률에서 여러 조문이 의제할 수 있다(사전결정·건축허가 등)
            continue
        if src["source"] == "ordin":
            if l["from_doc"] not in region_docs:
                continue
            if l["kind"] == "조례연계" and ref.doc_id not in store.cited_docs(l["from_doc"]):
                continue   # lnkLsOrdJo 잡음
            out.append((l["from_doc"], l["from_path"], "조례"))
        elif rank(src["kind"]) > rank(me["kind"]) and family(src["name"]) == family(me["name"]):
            # 하위 = 같은 법령 계열의 시행령·시행규칙. 다른 법령이 이 조문을 인용하는 것은 하위가 아니다
            out.append((l["from_doc"], l["from_path"], "하위"))
    # 같은 조에서 나온 여러 연결은 하나로(가장 얕은 경로), 위임 문구를 받는 조문을 앞으로
    best = {}
    for nd, np, rel in out:
        key = (nd, _jo(np))
        if key not in best or np.count("/") < best[key][1].count("/"):
            best[key] = (nd, np, rel)

    def score(item):
        nd, np, rel = item
        u = store.unit(nd, _jo(np)) if not np.startswith("별표") else None
        hinted = bool(u and DELEGATION_HINT.search(u["text"] or ""))
        return (rel != "조례", not hinted, np.startswith("별표"), np.count("/"))
    out = sorted(best.values(), key=score)
    # 이 허가를 의제하는 다른 법률 — 많으면(개발행위허가는 120여 개) 많이 인용되는 법률(일반법) 순으로
    ranked = sorted(deemed.items(), key=lambda kv: -_cited_by(store, kv[0]))
    for nd, paths in ranked[:DEEMED_BY_MAX]:
        for np in paths[:3]:
            out.append((nd, np, "의제됨", _deem_note(store, nd, np, "down")))
    if len(ranked) > DEEMED_BY_MAX:
        for nd, paths in ranked[DEEMED_BY_MAX:DEEMED_BY_MAX + 3]:
            res.more.append({"law": store.doc(nd)["name"], "article": paths[0],
                             "이유": f"이 조문의 허가 등을 의제하는 다른 법률(모두 {len(ranked)}개)"})
    if ORDIN_DELEGATION.search(ref.excerpt) and me["source"] == "law" and not region_doc_id:
        res.more.append({"law": ref.law, "article": ref.article,
                         "이유": "조례 위임 있음 — 지역 조례가 질문에 필요할 때만 region(시군구)을 넣어 다시 조회"})
    return out


def follow(store, doc_id: int, path: str, region_doc_id: int | None = None,
           direction: str = "up", depth: int = 2) -> FollowResult:
    res = FollowResult()
    start = node(store, doc_id, path)
    if not start:
        return res
    start.relation = "시작"
    q = deque([(start, 0)])
    res.visited.add((doc_id, path))
    while q:
        ref, d = q.popleft()
        res.refs.append(ref)
        if d >= depth or direction == "none":
            continue
        nbrs = []
        if direction in ("up", "both"):
            nbrs += _up(store, ref)
        if direction in ("down", "both"):
            nbrs += _down(store, ref, region_doc_id, res)
        # 위임·상하위 관계를 같은 층위의 단순 인용보다 먼저 담는다
        nbrs.sort(key=lambda x: REL_PRIORITY.get(x[2], 2))
        fresh = []
        for nd, np, rel, *note in nbrs:
            if (nd, np) in res.visited or any(_related(np, vp) for vd, vp in res.visited if vd == nd):
                continue
            res.visited.add((nd, np))
            fresh.append((nd, np, rel, note[0] if note else ""))
        for nd, np, rel, _ in fresh[MAX_NEIGHBORS:]:
            doc = store.doc(nd)
            res.more.append({"law": doc["name"], "article": np, "이유": "연결이 많아 생략"})
        for nd, np, rel, note in fresh[:MAX_NEIGHBORS]:
            nref = node(store, nd, np)
            if nref:
                nref.relation = rel
                nref.note = note
                q.append((nref, d + 1))
    return res
