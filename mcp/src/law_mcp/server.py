"""law-mcp 서버 진입점 — streamable HTTP(/mcp) + /health.

도구 모듈은 ``register(mcp, get_store) -> None`` 계약을 지킨다.
"""
from importlib.metadata import version as _pkg_version

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from law_mcp.config import get_settings
from law_mcp.store.db import Store, StoreUnavailable

INSTRUCTIONS = """\
건축·개발 인허가의 법령 근거를 찾는 서버다. 법령·시행령·시행규칙·자치법규(조례)와 별표를
로컬 색인에서 조회하고, 위임("대통령령으로", "조례로 정한다")과 인용("영 제71조")을 따라가
상위·하위 근거를 함께 돌려준다.
- 주소만 있으면 parcel_zoning 으로 용도지역·지목을 확인하거나, building_basis 에 address 로 넘긴다.
- "무엇을 지을 수 있나"는 building_basis 하나로 시행령 별표·조례 별표·상위법·건폐율·용적률까지 나온다. 지을 것 없이 건폐율·용적률만이면 zone_ratios.
- 특정 조문·별표는 law_article, 키워드로 찾을 때는 law_search, 용어의 뜻은 law_definition.
- 그린벨트·김영란법처럼 법령에 그대로 안 나오는 일상어·별칭은 term_link 로 법령 용어·법령명을 확인하라.
- 결과의 의제("…를 받은 것으로 본다") 관계: 의제·의제됨·의제_목록 은 원문 문구 그대로 설명하라.
- 결과의 facts.더_볼_연결 은 law_article 에 그대로 넣을 수 있는 인자다. 질문과 관련 있으면 이어서 조회하라.
- 이 서버는 허가 여부를 판정하지 않는다. 근거상태와 조문을 근거로 설명하라.
"""

_store_cache: dict = {}


def get_store() -> Store:
    path = get_settings().law_db_path
    st = _store_cache.get(path)
    if st is None:
        st = Store(path)
        _store_cache.clear()
        _store_cache[path] = st
    return st


def _tool_modules():
    from law_mcp.tools import article, building_basis, definition, parcel, search, term_link
    return (building_basis, parcel, article, search, definition, term_link)


def build_server() -> MCPServer:
    mcp = MCPServer(name="law-mcp", version=_pkg_version("law-mcp"), instructions=INSTRUCTIONS)
    for module in _tool_modules():
        module.register(mcp, get_store)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        try:
            return JSONResponse({"status": "ok", **get_store().health()})
        except StoreUnavailable as e:
            return JSONResponse({"status": "unavailable", "error": str(e)}, status_code=503)

    return mcp


def build_app():
    s = get_settings()
    _store_cache.clear()
    security = None
    if s.mcp_allowed_hosts or s.mcp_allowed_origins:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                             allowed_hosts=s.mcp_allowed_hosts,
                                             allowed_origins=s.mcp_allowed_origins)
    return build_server().streamable_http_app(stateless_http=True, host=s.mcp_host,
                                              transport_security=security)


def main():
    s = get_settings()
    uvicorn.run(build_app(), host=s.mcp_host, port=s.mcp_port)


if __name__ == "__main__":
    main()
