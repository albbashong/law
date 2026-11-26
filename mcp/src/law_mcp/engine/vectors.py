"""뜻으로 찾기 — 법령 조문을 임베딩해 두고, 검색어 임베딩과 코사인이 가까운 조문을 찾는다.

글자 조각(trigram)·낱말 색인은 검색어와 조문이 같은 말을 써야 찾는다. 사람은 일상어("해외주식으로 번 돈", "보도블럭")로
묻고 법령은 법령 용어("양도소득", "도로")로 쓰므로, 말이 다르면 맞는 조문에 닿지 못했다. 임베딩은 뜻이 가까운 글을 가깝게
놓으므로 이 간격을 규칙·낱말 목록 없이 메운다(자동 평가 2026-11-17 일상어 질문 270개: 10위 안 trigram 14% · 임베딩 78%).

색인은 lawdb.sqlite 와 따로 둔 디렉터리(VECTOR_DIR)다 — ids.npy(unit_id), vec.npy(정규화한 float16), hash.npy(임베딩한 글의
해시), meta.json(모델·차원). 다시 만들 때 글이 그대로인 조문은 앞 벡터를 그대로 쓴다(개정분만 임베딩). 조문 단위(항·호는
조문 본문에 들어 있다), 법령(source=law)만 — 자치법규는 아직 넣지 않았다.

임베딩은 OpenAI 호환 /embeddings(EMBED_URL·EMBED_MODEL)로 받는다. 검색어에는 EMBED_QUERY_PREFIX 를 붙인다(지시형 임베딩
모델은 검색어와 문서를 다르게 넣어야 순위가 맞는다)."""
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from law_mcp.config import get_settings

logger = logging.getLogger(__name__)

BODY_CHARS = 1000      # 조문 본문은 앞쪽만 — 긴 조문의 뒤쪽(부칙성 단서·표)은 뜻을 흐린다
SOURCE_SQL = ("SELECT u.unit_id, d.name, u.label, u.title, u.text FROM unit u JOIN doc d ON d.doc_id=u.doc_id"
              " WHERE d.source='law' AND u.path NOT LIKE '%/%' ORDER BY u.unit_id")
PENDING = "pending.npz"   # 만드는 중 쌓은 벡터 — 중간에 끊겨도 다음에 이어서 만든다
CHECKPOINT = 20           # 이만큼 묶음마다 쌓인 벡터를 저장
BLOCK = 50000             # 점수를 이만큼씩 나눠 계산(메모리)

_loaded: dict = {}
_lock = threading.Lock()


def doc_text(law: str, label: str, title: str | None, text: str | None) -> str:
    return f"{law} {label} {title or ''}\n{(text or '')[:BODY_CHARS]}"


def _digest(text: str) -> str:
    # 16진 문자열로 — 바이트(S20)로 두면 numpy 가 끝의 0 바이트를 떼어 같은 글을 다른 글로 본다
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def embed(texts: list[str], *, query: bool = False) -> np.ndarray:
    """글 → 정규화한 float32 벡터(행). 검색어면 지시문을 앞에 붙인다."""
    s = get_settings()
    inputs = [s.embed_query_prefix + t for t in texts] if query else list(texts)
    r = httpx.post(f"{s.embed_url.rstrip('/')}/embeddings", json={"model": s.embed_model, "input": inputs},
                   headers={"Authorization": f"Bearer {s.embed_api_key}"} if s.embed_api_key else None,
                   timeout=s.embed_timeout_sec)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    x = np.asarray([d["embedding"] for d in data], dtype=np.float32)
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)


def _read(out: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict] | None:
    try:
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        return (np.load(out / "ids.npy"), np.load(out / "vec.npy"), np.load(out / "hash.npy"), meta)
    except (OSError, ValueError, KeyError):
        return None


def build_vector_index(src: Path, out: Path, batch: int = 128, log=print) -> None:
    """lawdb.sqlite 의 법령 조문을 임베딩해 out 에 둔다. 앞 색인이 같은 모델이면 글이 그대로인 조문은 다시 임베딩하지 않는다."""
    out.mkdir(parents=True, exist_ok=True)
    model = get_settings().embed_model
    known: dict[str, np.ndarray] = {}
    prev = _read(out)
    if prev and prev[3].get("model") == model:
        known.update(zip(prev[2].tolist(), prev[1]))
    if (out / PENDING).exists():
        p = np.load(out / PENDING)
        known.update(zip(p["hash"].tolist(), p["vec"]))
    db = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    rows = [(uid, doc_text(n, l, t, x)) for uid, n, l, t, x in db.execute(SOURCE_SQL)]
    stamp = (db.execute("SELECT v FROM meta WHERE k='built_at'").fetchone() or [None])[0]
    db.close()
    hashes = [_digest(t) for _, t in rows]
    todo = [i for i, h in enumerate(hashes) if h not in known]
    log(f"{len(rows)} articles, {len(rows) - len(todo)} reused, {len(todo)} to embed")
    fresh_h, fresh_v, t0 = [], [], time.perf_counter()
    for n, k in enumerate(range(0, len(todo), batch), start=1):
        part = todo[k:k + batch]
        vecs = embed([rows[i][1] for i in part]).astype(np.float16)
        for i, vec in zip(part, vecs):
            known[hashes[i]] = vec
            fresh_h.append(hashes[i])
            fresh_v.append(vec)
        if n % CHECKPOINT == 0:
            np.savez(out / PENDING, hash=np.asarray(fresh_h, dtype="U40"), vec=np.asarray(fresh_v))
            log(f"{k + len(part)} / {len(todo)} {time.perf_counter() - t0:.0f}s")
    ids = np.asarray([uid for uid, _ in rows], dtype=np.int64)
    vec = np.asarray([known[h] for h in hashes], dtype=np.float16)
    for name, arr in (("ids", ids), ("vec", vec), ("hash", np.asarray(hashes, dtype="U40"))):
        np.save(out / f"{name}.tmp.npy", arr)
        os.replace(out / f"{name}.tmp.npy", out / f"{name}.npy")
    (out / "meta.json").write_text(json.dumps({"model": model, "dim": int(vec.shape[1]) if len(vec) else 0,
                                               "count": len(ids), "body_chars": BODY_CHARS,
                                               "built_at": stamp}), encoding="utf-8")
    (out / PENDING).unlink(missing_ok=True)
    _loaded.pop(str(out), None)
    log(f"done {len(ids)}")


def _index(out: Path):
    """(ids, float32 벡터) — 없거나 모델이 설정과 다르면 None(부르는 쪽은 다른 검색으로 찾는다). 한 번 읽어 둔다."""
    key = str(out)
    with _lock:
        if key not in _loaded:
            got = _read(Path(out))
            if got is None:
                logger.warning("vector index not found: %s", out)
                _loaded[key] = None
            elif got[3].get("model") != get_settings().embed_model:
                logger.warning("vector index model %s != EMBED_MODEL %s", got[3].get("model"), get_settings().embed_model)
                _loaded[key] = None
            else:
                _loaded[key] = (got[0], got[1].astype(np.float32), got[3].get("built_at"))
        return _loaded[key]


def warm(out: Path) -> None:
    _index(out)


def search(out: Path, query: str, limit: int, source: str | None = None) -> list[dict] | None:
    """[{kind: "u", ref: unit_id, score}] 를 가까운 순으로. 색인이나 임베딩을 못 쓰면 None.

    source 는 지금 쓰는 DB 의 빌드 시각 — 색인이 다른 빌드에서 만들어졌으면 조문 번호가 어긋나므로 쓰지 않는다."""
    idx = _index(out)
    if idx is None:
        return None
    ids, vec, stamp = idx
    if source is not None and stamp != source:
        logger.warning("vector index is from DB build %s, not %s — rebuild it (scripts/build_vectors.py)", stamp, source)
        return None
    try:
        q = embed([query], query=True)[0]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("query embedding failed: %s", e)
        return None
    scores = np.concatenate([vec[i:i + BLOCK] @ q for i in range(0, len(vec), BLOCK)]) if len(vec) else np.zeros(0)
    k = min(limit, len(scores))
    if k <= 0:
        return []
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [{"kind": "u", "ref": int(ids[i]), "score": float(scores[i])} for i in top]
