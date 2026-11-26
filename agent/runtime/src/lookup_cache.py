"""같은 질문 다시 묻기 — 검사를 통과한 턴의 (정리된 질문 → 조회 목록)을 LOOKUP_CACHE_TTL_SEC 동안 들고 있는다.

답은 들고 있지 않는다 — 다시 물으면 이 조회를 다시 실행해 원문을 새로 받고 그 원문으로 답한다.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_items: dict[str, dict[str, Any]] = {}
_loaded = False


def key(question: str) -> str:
    return re.sub(r"[\s\W_]+", "", question or "")


def _file() -> Path | None:
    # {agent} 는 에이전트 이름 — 같은 .env 로 띄운 여러 에이전트가 한 파일을 서로 덮어쓰지 않게
    return Path(settings.lookup_cache_file.format(agent=settings.agent_name)) if settings.lookup_cache_file else None


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    f = _file()
    if f and f.exists():
        try:
            _items.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning("lookup cache unreadable: %s", exc)


def _save() -> None:
    f = _file()
    if not f:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(_items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as exc:
        logger.warning("lookup cache not saved: %s", exc)


def get(question: str, now: float | None = None) -> list[dict[str, Any]] | None:
    k = key(question)
    if settings.lookup_cache_ttl_sec <= 0 or not k:
        return None
    now = time.time() if now is None else now
    with _lock:
        _load()
        hit = _items.get(k)
        if hit and now - hit["at"] > settings.lookup_cache_ttl_sec:
            _items.pop(k, None)
            hit = None
        return list(hit["calls"]) if hit else None


def put(question: str, calls: list[dict[str, Any]], now: float | None = None) -> None:
    k = key(question)
    if settings.lookup_cache_ttl_sec <= 0 or not k or not calls:
        return
    with _lock:
        _load()
        _items.pop(k, None)
        _items[k] = {"at": time.time() if now is None else now,
                     "calls": [{"tool": c["tool"], "args": dict(c.get("args") or {})} for c in calls]}
        while len(_items) > settings.lookup_cache_max:
            _items.pop(next(iter(_items)))
        _save()


def clear(memory_only: bool = False) -> None:
    global _loaded
    with _lock:
        _items.clear()
        _loaded = False
        if not memory_only:
            f = _file()
            if f and f.exists():
                f.unlink()
