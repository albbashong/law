"""법제처 국가법령정보 공동활용(DRF) — 일상용어–법령용어 연계, 법령명 검색.

사용자가 쓴 일상어·별칭("그린벨트", "김영란법")을 법령이 쓰는 용어·법령명에 잇는 데 쓴다. 모델의 기억 대신
법제처가 관리하는 연계 자료로 정하려는 것이다. 같은 말은 프로세스 안에서 한 번만 묻는다.
"""
import time
from urllib.parse import parse_qs, urlparse

import httpx

BASE = "https://www.law.go.kr/DRF"


class LawgoUnavailable(Exception):
    pass


def _rows(v) -> list[dict]:
    """한 건이면 객체, 여러 건이면 목록으로 오는 필드를 목록으로."""
    return [x for x in (v if isinstance(v, list) else [v] if v else []) if isinstance(x, dict)]


class Client:
    def __init__(self, oc: str, timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                 retries: int = 3, retry_wait: float = 0.5, law_names_max: int = 3):
        self.oc = oc
        self.retries, self.retry_wait, self.law_names_max = retries, retry_wait, law_names_max
        self.http = httpx.Client(base_url=BASE, timeout=timeout, transport=transport)
        self._cache: dict[tuple, object] = {}

    def _get(self, path: str, params: dict) -> dict:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.http.get(path, params={"OC": self.oc, "type": "JSON", **params})
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as e:
                last = e
            if attempt + 1 < self.retries:
                time.sleep(self.retry_wait * (attempt + 1))
        raise LawgoUnavailable(f"{path}: {last}")

    def _cached(self, key: tuple, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def daily_term_links(self, word: str) -> list[dict]:
        """일상용어 word 와 같은 이름의 항목이 잇는 법령용어 — [{"법령용어", "관계"(동의어·연관어 …)}]."""
        def fetch():
            found = self._get("/lawSearch.do", {"target": "dlytrm", "query": word, "display": 100})
            rows = _rows((found.get("dlytrmSearch") or {}).get("일상용어"))
            same = [r for r in rows if r.get("일상용어명") == word]
            out: list[dict] = []
            for r in same:
                mst = (parse_qs(urlparse(r.get("용어간관계링크") or "").query).get("MST") or [""])[0]
                if not mst:
                    continue
                rel = self._get("/lawService.do", {"target": "dlytrmRlt", "MST": mst})
                for d in _rows((rel.get("dlytrmRltService") or {}).get("일상용어")):
                    for x in _rows(d.get("연계용어")):
                        item = {"법령용어": x.get("법령용어명", ""), "관계": x.get("용어관계", "")}
                        if item["법령용어"] and item not in out:
                            out.append(item)
            return out
        return self._cached(("dlytrm", word), fetch)

    def law_names(self, word: str) -> list[dict]:
        """법제처 법령명 검색 — 정식명·약칭·별칭으로 찾은 법령 [{"법령", "약칭"}] (앞에서 law_names_max 건)."""
        def fetch():
            found = self._get("/lawSearch.do", {"target": "law", "query": word, "display": self.law_names_max})
            return [{"법령": r.get("법령명한글", ""), "약칭": r.get("법령약칭명") or ""}
                    for r in _rows((found.get("LawSearch") or {}).get("law")) if r.get("법령명한글")]
        return self._cached(("law", word), fetch)
