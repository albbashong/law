"""설정 — 환경변수 UPPER_SNAKE (예: ``law_db_path`` → ``LAW_DB_PATH``)."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    law_db_path: Path = ROOT / "data" / "lawdb.sqlite"
    facts_max_chars: int = 7600
    excerpt_chars: int = 400
    follow_depth: int = 2
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 7017
    mcp_allowed_hosts: list[str] = []
    mcp_allowed_origins: list[str] = []
    vworld_api_key: str = ""
    law_api_oc: str = ""              # 법제처 DRF 인증값 — 말 연계(term_link) 조회용, 없으면 그 도구는 실패를 돌려준다
    lawgo_timeout: float = 10.0
    vworld_timeout: float = 10.0
    vworld_retries: int = 15          # 정상 키에도 INCORRECT_KEY 가 자주(관측 약 65%) 와서 넉넉히 다시 묻는다
    vworld_retry_wait: float = 0.2    # 다시 묻기 사이 대기(초) — 늘어나되 1초를 넘지 않는다
    # 법령명 오타 보정 — 못 찾은 이름과 가장 가까운 법령명의 닮음(0~1)이 이 값 이상이고 다음 후보보다
    # 이만큼 앞서면 그 법령으로 조회한다("야생동물 보호 …" → "야생생물 보호 …"). 0 이면 보정하지 않는다.
    law_name_fix_ratio: float = 0.9
    law_name_fix_margin: float = 0.05
    # 필지 규제 이름이 이보다 많은 조문 제목에 나오면(도로·공원처럼 흔한 말) 근거 조문을 붙이지 않는다 — 이름만 남긴다
    regulation_title_df_max: int = 1000
    # law_search 방식 — trigram(lawdb 의 세 글자 조각 색인) · words(형태소 낱말 색인, words_db_path) · hybrid(둘을 순위 융합).
    # 낱말 색인 파일이나 분석기가 없으면 trigram 으로 찾는다. 낱말 색인은 scripts/build_words.py 로 만든다.
    search_method: str = "trigram"
    words_db_path: Path = ROOT / "data" / "lawdb_words.sqlite"
    # 결과를 법령·자치법규로 나눠(각 limit 개, 법령 먼저) 담는다 — 수가 10배 넘는 시군 조례가 법령 조문을 덮지 않게
    search_split_sources: bool = False
    words_title_weight: float = 10.0  # 제목 칸 가중 — 실측(자동 평가 300문항) 2·5·10 중 10 이 가장 나았다
    # 도구 결과 최상위에 두는 '답에 꼭 나와야 할 이름' 키 — 모델에게는 안 보이고 런타임이 답을 점검할 때 쓴다
    mention_key: str = "mention"


def get_settings() -> Settings:
    return Settings()
