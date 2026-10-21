"""브이월드 — 주소 → PNU → 용도지역(토지이용계획)·지목/면적(토지특성)."""
from dataclasses import dataclass, field

import time

import httpx

BASE = "https://api.vworld.kr"


class AddressNotFound(Exception):
    pass


class VworldUnavailable(Exception):
    pass


@dataclass
class Parcel:
    address: str
    pnu: str
    sido: str
    sigungu: str
    zones: list[str] = field(default_factory=list)
    others: list[str] = field(default_factory=list)
    land_category: str | None = None
    area: str | None = None
    notes: list[str] = field(default_factory=list)


class Client:
    def __init__(self, key: str, timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                 retries: int = 6, retry_wait: float = 0.4):
        self.key = key
        self.retries, self.retry_wait = retries, retry_wait
        self.http = httpx.Client(base_url=BASE, timeout=timeout, transport=transport)

    @staticmethod
    def _error_of(data: dict) -> str | None:
        """브이월드 오류 응답이면 코드. 정상 키에도 간헐적으로 INCORRECT_KEY 가 와서 재시도 대상이다."""
        for v in data.values():
            if isinstance(v, dict):
                code = v.get("resultCode") or v.get("error")
                if v.get("status") == "ERROR":
                    code = (v.get("error") or {}).get("code", "ERROR")
                if code and code not in ("", "OK", "NOT_FOUND"):
                    return str(code)
        return None

    def _get(self, path: str, params: dict) -> dict:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.http.get(path, params={**params, "key": self.key, "format": "json"})
                r.raise_for_status()
                data = r.json()
                err = self._error_of(data)
                if not err:
                    return data
                last = err
            except (httpx.HTTPError, ValueError) as e:
                last = e
            if attempt + 1 < self.retries:
                time.sleep(self.retry_wait * (attempt + 1))
        raise VworldUnavailable(f"{path}: {last}")

    def geocode(self, address: str) -> dict:
        for typ in ("PARCEL", "ROAD"):
            d = self._get("/req/address", {"service": "address", "request": "getCoord", "version": "2.0",
                                           "crs": "epsg:4326", "type": typ, "refine": "true",
                                           "simple": "false", "address": address})["response"]
            if d.get("status") == "OK":
                return d
            if d.get("status") == "ERROR":
                raise VworldUnavailable(str(d.get("error")))
        raise AddressNotFound(address)

    def _road_pnu(self, address: str) -> str | None:
        d = self._get("/req/search", {"service": "search", "request": "search", "version": "2.0",
                                      "type": "address", "category": "road", "size": 1,
                                      "query": address})["response"]
        items = ((d.get("result") or {}).get("items")) or []
        pid = items[0].get("id") if items else None
        return pid if pid and len(pid) == 19 else None

    def lookup(self, address: str) -> Parcel:
        g = self.geocode(address)
        st = g["refined"]["structure"]
        pnu = st.get("level4LC") or ""
        if len(pnu) != 19:   # 도로명 주소: 검색 API의 id 가 필지 PNU 다
            pnu = self._road_pnu(g["refined"].get("text") or address) or pnu
        p = Parcel(g["refined"].get("text", address), pnu, st.get("level1", ""), st.get("level2", ""))
        if len(pnu) != 19:
            p.notes.append("도로명 주소라 PNU를 확정하지 못했습니다. 지번 주소로 다시 조회하세요.")
            return p
        uses = self._get("/ned/data/getLandUseAttr", {"pnu": pnu, "numOfRows": 100, "pageNo": 1})
        for f in (uses.get("landUses") or {}).get("field") or []:
            name, rel = f.get("prposAreaDstrcCodeNm", ""), f.get("cnflcAtNm", "")
            if name.endswith("지역") and rel in ("포함", "저촉") and name not in p.zones:
                p.zones.append(name)
            elif name not in p.others:
                p.others.append(name if rel == "포함" else f"{name}({rel})" if rel else name)
        try:
            ch = self._get("/ned/data/getLandCharacteristics", {"pnu": pnu, "numOfRows": 100, "pageNo": 1})
            rows = (ch.get("landCharacteristicss") or {}).get("field") or []
            if rows:
                latest = max(rows, key=lambda x: x.get("stdrYear", ""))
                p.land_category, p.area = latest.get("lndcgrCodeNm"), latest.get("lndpclAr")
            else:
                p.notes.append("지목·면적을 확인하지 못했습니다(토지특성 조회 불가).")
        except VworldUnavailable:
            p.notes.append("지목·면적을 확인하지 못했습니다(토지특성 조회 불가).")
        # "경상북도 영천시"처럼 level2가 시군구, 일반구가 있으면 "수원시 장안구" 형태
        p.others = [o for o in p.others if o]
        return p
