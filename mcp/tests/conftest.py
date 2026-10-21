import pytest

from indexer.build import build

SUBSET = [
    ("law", "284013"),    # 국토의 계획 및 이용에 관한 법률
    ("law", "288857"),    # 같은 법 시행령
    ("law", "273437"),    # 건축법
    ("law", "290833"),    # 건축법 시행령
    ("law", "284301"),    # 농지법
    ("law", "289913"),    # 산지관리법
    ("law", "284369"),    # 산업집적활성화 및 공장설립에 관한 법률 — 용도 정의어로 인허가 찾기
    ("law", "276485"),    # 기업활동 규제완화에 관한 특별조치법 — "공장"을 함께 정의하는 특례법(잡음)
    ("law", "230205"),    # 공사중단 장기방치 건축물 정비법 — lnkLsOrdJo 잡음 확인용
    ("ordin", "2000711"),  # 영천시 도시계획 조례
    ("ordin", "1971041"),  # 영천시 건축 조례
    ("ordin", "2102141"),  # 수원시 도시계획 조례 — 건축제한을 본문 조문에 둔다
    ("ordin", "2149501"),  # 서울특별시 도시계획 조례
    ("ordin", "2120465"),  # 부산광역시 도시계획 조례
]


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory):
    out = tmp_path_factory.mktemp("lawdb") / "lawdb.sqlite"
    build(out, docs=SUBSET, log=lambda m: None)
    return out


@pytest.fixture(scope="session")
def store(fixture_db):
    from law_mcp.store.db import Store
    return Store(fixture_db)
