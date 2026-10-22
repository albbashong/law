"""에이전트 런타임 설정 — 코드는 하나, 에이전트마다 설정 디렉터리를 갈아끼운다.

`AGENT_DIR` 이 가리키는 디렉터리에서 `.env`·`prompt.txt`·`agent.yaml` 을 읽는다.
에이전트가 여섯으로 늘어도 이 파일은 그대로다 — 늘어나는 것은 설정 디렉터리뿐이다.
"""

import json
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AGENT_DIR = Path(os.environ.get("AGENT_DIR", ".")).resolve()


def _csv(raw: str) -> tuple[str, ...]:
    """쉼표 구분 설정값 → 빈 항목을 뺀 튜플."""
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(AGENT_DIR / ".env"), extra="ignore")

    agent_name: str = Field(default="agent", description="서비스 이름. 로그·FastAPI title 에 쓴다.")
    host: str = "0.0.0.0"
    port: int = Field(
        default=8130, description="8101~8112 는 다른 스택이 쓴다 — 겹치지 않게 고른다.",
    )

    vllm_base: str = "http://localhost:8000/v1"
    vllm_model: str = "gpt-oss-120b"
    vllm_api_key: str = ""
    reasoning_effort: str = Field(
        default="low",
        description="추론 강도 힌트. gpt-oss 는 추론을 끌 수 없고 강도만 조절된다 — 실측 "
        "2026-08-31: 미지정 5.9초/312토큰, low 1.1초/92토큰으로 5배 차이가 났다. 강도가 높으면 "
        "사고 토큰이 출력 상한을 잠식해 툴콜이 잘리기도 한다. 빈 문자열이면 보내지 않는다.",
    )
    vllm_temperature: float = Field(
        default=0.0, description="결정론이 전제다 — 전후 비교가 흔들리면 판단을 못 한다.",
    )

    # 붙을 MCP 서버. JSON 객체 {이름: URL}. 도구 이름이 서버 간에 겹치지 않아야
    # 이름만으로 라우팅할 수 있다 — 겹치면 기동 시점에 걸러 로그로 알린다.
    mcp_servers: str = Field(
        default="{}",
        description='예: {"map_control": "http://localhost:7012/mcp"}',
    )
    # 특정 서버의 도구 중 일부만 모델에 보일 때 쓴다. JSON {서버이름: [도구, ...]}.
    # 비우면 그 서버의 도구를 전부 보여준다.
    mcp_tool_allowlist: str = Field(default="{}")

    rounds_max: int = Field(
        default=8,
        description="한 질의가 도는 최대 라운드. 코어의 호출 시한 안에 끝나야 한다.",
    )
    tool_result_max_chars: int = Field(
        default=9000,
        description="모델에 되먹일 도구 결과의 상한(자). 0 이면 안 자른다. facts 계약을 따르는 "
        "결과는 facts·message 만 실리므로(permit MCP 예산 7,600 + message 300) 9,000 이면 "
        "잘리지 않는다. 계약이 없는 결과는 앞에서부터 잘린다 — 실측 2026-08-28: 21,519자 "
        "응답을 그대로 넣으면 모델이 답을 못 만들었다. 성형에는 자르지 않은 원본이 간다.",
    )
    task_ttl_sec: int = Field(
        default=3600,
        description="종결된 태스크를 보존하는 시간(초). 지나면 정리한다 — 안 하면 무한히 쌓인다.",
    )
    max_tasks: int = Field(default=1000, description="보존 태스크 개수 상한.")
    tool_retry_max: int = Field(
        default=2,
        description="같은 (도구, 인자) 를 모델이 다시 시도할 수 있는 횟수. 넘으면 반복 "
        "차단 문구로 바꿔 되묻기로 빠질 길을 준다. 근거는 판단이지 실측이 아니다.",
    )

    # ── 되묻기 성형 (스펙 §5.3). 어느 도구의 결과를 되묻기 원천으로 볼지는 에이전트마다
    # 다르므로 코드가 아니라 설정이 정한다.
    clarify_tools: str = Field(
        default="",
        description="되묻기 상태를 낼 수 있는 도구 이름(쉼표 구분). 비우면 도구 기반 되묻기 없음.",
    )
    clarify_status_values: str = Field(
        default="clarify,needs_disambiguation",
        description="도구 결과의 status 가 이 값이면 되묻기로 본다(쉼표 구분).",
    )
    candidate_id_keys: str = Field(
        default="pnu,id,code,sku",
        description="후보 항목에서 식별자로 읽을 키(쉼표 구분, 앞 순서 우선). 규격 4.2절 — "
        "후보는 유일한 식별자와 서로 구별되는 표시를 가져야 한다.",
    )
    resolve_drop_keys: str = Field(
        default="steps,_pwmcp_meta",
        description="resolve 아티팩트에서 뺄 키(쉼표 구분). 단계별 업스트림 원문처럼 부피만 "
        "큰 것 — 폴링마다 실려 나가므로 뺀다.",
    )
    fail_unavailable_text: str = Field(
        default="{tool} 에 연결하지 못했습니다: {message}",
        description="의존 서비스에 닿지 못해 태스크를 FAILED 로 끝낼 때 status.message 에 쓰는 "
        "형식. 규격 4.3절 — '실패했습니다' 만 적지 않는다. 사용자 문장이라 코드가 아니라 "
        "설정에 둔다.",
    )

    # ── 답변 인용 검증. 무엇을 "인용"으로 볼지는 에이전트 영역이라 코드가 아니라 설정이 정한다.
    answer_cite_pattern: str = Field(
        default="",
        description="답변에서 인용(예: 조문 번호)을 잡는 정규식. 잡힌 인용이 이번 대화의 도구 결과·"
        "사용자 발화에 없으면 모델에 한 번 되돌려 확인시킨다. 비우면 검증하지 않는다.",
    )
    answer_pair_pattern: str = Field(
        default="",
        description="인용을 그 앞말과 함께 잡는 정규식 — 이름 그룹 scope(앞말)·cite(인용). 앞말이 도구 결과의 "
        "어떤 출처명(ANSWER_PAIR_KEYS 첫 키)으로 끝나는데 그 출처의 인용(둘째 키)에 없으면 되돌린다.",
    )
    answer_pair_keys: str = Field(
        default="",
        description="도구 결과에서 (출처명, 인용) 짝을 읽을 키 두 개(쉼표 구분). 예: law,article",
    )
    answer_unverified_text: str = Field(
        default="",
        description="되돌린 뒤에도 남은 인용을 답변 끝에 밝히는 문장({cites}). 비우면 덧붙이지 않는다.",
    )
    answer_cite_retry_text: str = Field(
        default="답변에 쓴 다음 인용은 이번 대화의 도구 결과에 없습니다: {cites}. "
        "도구로 조회해 확인하거나, 확인할 수 없으면 답변에서 빼고 다시 답하세요.",
        description="검증에 걸린 인용을 모델에 되돌릴 때 쓰는 문장. {cites} 자리에 인용 목록.",
    )

    answer_law_pattern: str = Field(
        default="",
        description="답변에서 법령명을 잡는 정규식(비지 않은 그룹이 이름). 이름이 이번 대화의 도구 결과·사용자 발화에 "
        "없으면(공백 무시) 인용처럼 되돌린다 — 지어낸 법령명을 막는다. 비우면 검증하지 않는다.",
    )
    answer_strip_pattern: str = Field(
        default="", description="최종 답변에서 지울 표기의 정규식(예: 모델이 본문에 흘린 도구 호출 표기). 비우면 그대로 둔다.")

    # ── 도구 인자 검증. 어느 인자가 "사용자가 말한 값"이어야 하는지는 에이전트 영역이라 설정이 정한다.
    answer_rate_check: bool = Field(
        default=False, description="답에 쓴 비율(%, N분의 M, 1/N)이 이번 조회 원문의 비율 값과 맞는지 대조한다(값 비교).")
    answer_rate_retry_text: str = Field(
        default="답변의 다음 비율 수치는 조회한 원문의 어떤 값과도 맞지 않습니다: {rates}. 원문 표기(예: 1천분의 2.5)대로 "
        "쓰거나 정확히 환산해(1천분의 2.5 = 0.25%) 다시 답하세요.",
        description="비율 수치가 맞지 않을 때 모델에게 되돌리는 문장.")
    arg_grounded_keys: str = Field(
        default="",
        description="값이 사용자 발화·도구 결과에 나온 것이어야 하는 인자 이름(쉼표 구분, 예: address,region). "
        "숫자가 든 낱말은 모두, 나머지 낱말은 하나 이상 나와야 실행한다. 비우면 검증하지 않는다.",
    )
    arg_ungrounded_text: str = Field(
        default="실행하지 않았습니다 — 인자 {key}='{value}' 는 사용자 발화나 도구 결과에 없는 값입니다. "
        "사용자가 말한 값만 넘기세요. 필요한데 없으면 ask_user 로 묻거나, 그 인자 없이 답하세요.",
        description="검증에 걸린 인자를 모델에 되돌리는 문장({key}, {value}).",
    )

    # ── 작업 기억. 에이전트는 상태를 들지 않는다 — 답과 함께 memory 아티팩트를 내고, 호출자가 같은 대화의
    # 다음 호출에 data 파트(memory_key)로 돌려주면 그것을 이전 작업으로 읽는다.
    memory_key: str = Field(default="memory", description="작업 기억을 싣는 data 파트 키이자 아티팩트 이름.")
    memory_turns: int = Field(default=5, description="기억에 남기는 최근 턴 수. 0 이면 기억을 내지 않는다.")
    memory_answer_chars: int = Field(
        default=400, description="턴마다 남기는 답의 앞부분 길이(자). 근거 원문은 남기지 않는다 — 다시 "
        "조회할 호출 인자(calls)를 남기므로 원문은 필요할 때 다시 얻는다.",
    )
    memory_calls_max: int = Field(default=12, description="턴마다 남기는 도구 호출 수 상한.")
    memory_local: bool = Field(
        default=True,
        description="호출자가 기억을 돌려주지 않을 때 런타임이 contextId 별로 잠깐 들고 있는다(대비책). "
        "호출자가 memory 를 data 파트로 보내면 그것이 우선한다. 호출자가 지원하면 끈다.",
    )
    memory_local_max: int = Field(default=500, description="대비책이 들고 있는 대화(contextId) 수 상한.")
    memory_heading: str = Field(
        default="[이전 작업] 같은 대화에서 앞서 처리한 질문들입니다. 이어지는 질문이면 이것을 바탕으로 부족한 "
        "부분만 더 조회하세요. 여기 있는 조문을 다시 인용하려면 조회 인자로 다시 조회해 원문을 확인하세요.",
        description="이전 작업 블록의 머리말.",
    )
    story_prompt: str = Field(
        default="",
        description="이야기로 다시 쓰기 지시 파일(AGENT_DIR 기준). 정하면 모델에게 retell_as_story 도구를 주고, "
        "모델이 부르면 검증을 마친 답을 이 지시로 한 번 더 불러 이야기로 바꾼다. 비우면 끈다.",
    )
    story_request_text: str = Field(
        default="[질문]\n{question}\n\n[검증된 답]\n{answer}\n\n[조회 원문]\n{sources}",
        description="이야기 단계에 넘기는 사용자 메시지 틀. {sources} 는 이번 턴 도구 결과(예약 도구 제외).",
    )
    story_sources_max_chars: int = Field(
        default=30000, description="이야기 단계에 넘기는 조회 원문 상한(자). 넘으면 뒤를 자른다.")
    story_format_pattern: str = Field(
        default="", description="이야기가 맞춰야 할 형식(정규식, 처음부터 검사). 비우면 검사하지 않는다.")
    story_forms_dir: str = Field(
        default="", description="설명틀 디렉터리(AGENT_DIR 기준). 파일(*.txt)마다 첫 줄은 고르는 기준, 나머지는 쓰는 법. "
        "정하면 retell_as_story 에 form 인자(파일 이름 중 하나)를 주고, 고른 틀을 지시문의 {설명틀}·{설명틀_쓰는법}에 채운다.")
    story_law_keys: str = Field(
        default="", description="도구 결과에서 법령명으로 읽을 키(쉼표 구분) — 이야기 지시문의 {법령}에 채운다.")
    story_laws_max: int = Field(default=4, description="{법령}에 넣는 법령명 수 상한.")
    story_form_select_prompt: str = Field(
        default="", description="설명틀 고르기 지시 파일(AGENT_DIR 기준). 정하면 검증된 답을 본 뒤 틀 이름 하나를 고르게 한다"
        "({설명틀_목록} 자리에 '이름: 고르는 기준' 목록). 못 고르면 retell_as_story 의 form 을 쓴다.")
    story_review_prompt: str = Field(
        default="", description="이야기 점검 지시 파일(AGENT_DIR 기준). 정하면 쓴 이야기를 검증된 답과 대조해 JSON 의 "
        "STORY_REVIEW_KEY 목록으로 고칠 점을 받고, 있으면 그것을 붙여 다시 쓰게 한다(STORY_ATTEMPTS 안에서).")
    story_review_key: str = Field(default="문제", description="점검 JSON 에서 고칠 점 목록의 키.")
    story_review_request_text: str = Field(
        default="[질문]\n{question}\n\n[검증된 답]\n{answer}\n\n[조회 원문]\n{sources}\n\n[이야기]\n{story}",
        description="점검 단계에 넘기는 사용자 메시지 틀.")
    story_review_retry_text: str = Field(
        default="\n\n[고칠 점] 앞서 쓴 이야기를 점검했더니 다음 문제가 있었습니다. 고쳐서 처음부터 다시 쓰세요.\n{problems}",
        description="고칠 점을 다시 쓰기 요청 뒤에 붙이는 틀.")
    story_plan_prompt: str = Field(
        default="", description="이야기 계획 지시 파일(AGENT_DIR 기준). 정하면 이야기 전에 쓸 사유·관계를 JSON 으로 "
        "고르게 하고, 대조를 통과한 계획을 이야기 요청에 붙인다. 비우면 계획 없이 바로 쓴다.")
    story_plan_grounded_keys: str = Field(
        default="", description="계획 JSON 에서 값이 조회 원문에 그대로 있어야 하는 키(쉼표 구분).")
    story_plan_text: str = Field(
        default="\n\n[이야기 계획]\n{plan}", description="대조를 통과한 계획을 이야기 요청 뒤에 붙이는 틀.")
    story_attempts: int = Field(
        default=2, description="이야기가 비었거나 형식·인용 검증에 걸리면 다시 쓰게 하는 총 횟수. 모두 걸리면 사실 답.")
    story_ack_text: str = Field(
        default="알겠습니다. 근거를 조회해 평소 형식으로 사실을 정확히 답하세요. 앞 답을 쉽게 다시 설명해 달라는 "
        "요청이면, 앞 턴의 조회를 다시 실행한 결과가 이 뒤에 이어지니 그 결과로 앞 답과 같은 범위(위임 단계·조건·"
        "추가로 확인할 것)를 답하세요. 그 답을 이야기로 바꾸는 일은 다음 단계에서 따로 합니다.",
        description="retell_as_story 호출에 돌려주는 도구 결과.",
    )
    current_heading: str = Field(
        default="[현재 질문]",
        description="이전 작업 블록이 있을 때 이번 발화 앞에 붙이는 이름표 — 앞 질문과 이번 질문을 가른다.",
    )

    public_url: str = Field(
        default="",
        description="밖에서 닿는 주소. 프록시나 경로 접두사 뒤에 있으면 반드시 쓴다 — "
        "카드의 supportedInterfaces[0].url 이 이 값이 되고, 오케스트레이터는 그리로 "
        "요청을 보낸다. 비우면 http://{host}:{port} 를 쓴다.",
    )

    def servers(self) -> dict[str, str]:
        return json.loads(self.mcp_servers or "{}")

    def allowlist(self) -> dict[str, set[str]]:
        raw = json.loads(self.mcp_tool_allowlist or "{}")
        return {k: set(v) for k, v in raw.items()}

    def clarify_tool_names(self) -> tuple[str, ...]:
        return _csv(self.clarify_tools)

    def clarify_status_names(self) -> tuple[str, ...]:
        return _csv(self.clarify_status_values)

    def answer_pair_key_names(self) -> tuple[str, ...]:
        return _csv(self.answer_pair_keys)

    def arg_grounded_names(self) -> tuple[str, ...]:
        return _csv(self.arg_grounded_keys)

    def candidate_id_names(self) -> tuple[str, ...]:
        return _csv(self.candidate_id_keys)

    def resolve_drop_names(self) -> tuple[str, ...]:
        return _csv(self.resolve_drop_keys)


settings = Settings()
PROMPT_PATH = AGENT_DIR / "prompt.txt"
CARD_PATH = AGENT_DIR / "agent.yaml"


def public_base() -> str:
    """카드가 광고할 주소. 밖에서 닿는 것이어야 한다."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    host = "localhost" if settings.host in ("0.0.0.0", "") else settings.host
    return f"http://{host}:{settings.port}"
