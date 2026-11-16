# 검색 방법 에이전트(search_plan) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 질문마다 법령 데이터의 인용·위임 연결로 '열어 볼 조문 순서'(검색 계획)를 계산해 law_review 에 주고, 런타임이 꼭 열 조문을 열었는지 점검해 작은 모델도 같은 깊이로 찾게 한다.

**Architecture:** law-mcp 에 경로 계산 도구 `law_route`(기존 `engine/follow.py` 위)를 더한다. 새 A2A 에이전트 `agent/search_plan`(공용 runtime 재사용, 모델 루프 대신 `query.py` 의 정해진 순서: law_search → LLM 고르기 1회 → parcel_zoning(주소 있을 때) → law_route)이 계획을 facts 로 돌려준다. law_review 런타임은 `PLAN_AGENT_URL` 이 있으면 질문마다 먼저 계획을 받아 사용자 메시지 뒤에 붙이고, 답 점검 때 꼭 열 조문 중 안 연 것을 되돌린다.

**Tech Stack:** Python 3.13, uv, pydantic-settings, FastAPI(A2A runtime), MCP SDK v2(law-mcp), SQLite, ollama(OpenAI 호환 /v1), pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-search-plan-agent-design.md`

## Global Constraints

- 하드코딩·키워드 규칙 금지(법률 용어만 예외). 질문 낱말→조문 표, 도구 이름·법령명 리터럴을 판단 로직에 넣지 않는다. 문장·키·목록은 `.env` 설정값.
- 키·LLM 주소는 커밋 금지 — `.env.example` 에서는 비운다. `.env` 는 추적 안 함.
- permit-workflow · permit-workflow-prompt 수정 금지. HWP/PDF 저장 금지.
- `PLAN_AGENT_URL` 이 비면 law_review 는 지금과 **똑같이** 동작해야 한다.
- 기존 시험 통과 유지: 런타임 `cd agent/runtime; unset VIRTUAL_ENV; uv run pytest -q tests` (442), MCP `cd mcp; unset VIRTUAL_ENV; uv run pytest -q tests` (222).
- 작은 모델: 이 PC ollama(`OLLAMA_HOST`=0.0.0.0:9001, `http://localhost:9001/v1`), 모델 `qwen3:4b` · `qwen3:8b` · `gemma4:e4b`.
- search_plan 포트 7018. 커밋 끝줄 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. push 하지 않는다. `eval/playground.py` 는 건드리지 않는다.

## Review Focus

1. search_plan 이 죽었거나 느릴 때(시간 초과) — law_review 가 계획 없이 지금처럼 답해야 한다. → Task 5 `test_plan_agent_down_answers_as_before`.
2. LLM 이 후보 밖 조문·깨진 JSON 을 낼 때 — 빈 계획. → Task 4 `test_selection_outside_candidates_gives_empty_plan`, `test_broken_json_gives_empty_plan`.
3. 계획 조문을 `law_article` chain 발췌로만 받은 경우도 '열었음'으로 쳐야 한다(되돌림 남발 방지). → Task 5 `test_step_seen_in_chain_excerpt_counts_as_opened`.
4. 다턴 재개(되묻기에 답하는 턴)에서 계획을 다시 받지 않는다(재개는 이미 그 대화를 잇는다). → Task 5 `test_resume_does_not_fetch_plan_again`.
5. search_plan 이 부른 law-mcp 호출이 평가 recall 에 섞이면 안 된다. → Task 1 `test_trace_records_caller_from_query`, Task 7 `test_rows_from_ignored_caller_are_dropped`.

---

### Task 1: law-mcp 추적에 호출자 표시

search_plan 의 law-mcp 호출을 평가에서 가려내기 위해, MCP URL 의 쿼리 `?caller=<이름>` 을 추적 행 `caller` 로 남긴다.

**Files:**
- Modify: `mcp/src/law_mcp/server.py` (`build_app` 에 ASGI 미들웨어)
- Modify: `mcp/src/law_mcp/tools/common.py` (`trace` 가 `caller` 기록)
- Test: `mcp/tests/test_trace.py`

**Interfaces:**
- Produces: `law_mcp.tools.common.CALLER: contextvars.ContextVar[str]` (기본 ""). 추적 행에 `"caller": CALLER.get()`.

- [ ] **Step 1: 실패하는 시험** — `test_trace_records_caller_from_query`: `LAW_MCP_TRACE_FILE` 을 tmp 로 두고 `CALLER.set("search_plan")` 뒤 `trace("law_search", {}, result("ok", {}, ""))` → 마지막 행 `row["caller"] == "search_plan"`. 그리고 `test_app_sets_caller_from_query_string`: `build_app()` 을 Starlette `TestClient` 로 열어 `/health?caller=x` 요청 중 미들웨어가 `CALLER` 를 `"x"` 로 둠(미들웨어를 직접 호출하는 작은 ASGI 앱으로 확인해도 된다).
- [ ] **Step 2:** `cd mcp; unset VIRTUAL_ENV; uv run pytest -q tests/test_trace.py` → FAIL (`CALLER` 없음).
- [ ] **Step 3:** 구현. 미들웨어는 `scope["query_string"]` 의 `caller` 값을 요청 동안 `CALLER` 에 둔다(없으면 "").
- [ ] **Step 4:** 같은 명령 PASS, 전체 MCP 시험 PASS.
- [ ] **Step 5:** 커밋 `feat(trace): MCP URL 의 ?caller= 를 추적 행 caller 로 남김`.

### Task 2: law-mcp `law_route` — 조문 하나에서 열 순서 계산

**Files:**
- Create: `mcp/src/law_mcp/engine/route.py`
- Create: `mcp/src/law_mcp/tools/route.py` (`register(mcp, get_store)`)
- Modify: `mcp/src/law_mcp/server.py:_tool_modules` (route 추가), `mcp/src/law_mcp/config.py`
- Test: `mcp/tests/test_route.py`, `mcp/tests/test_registration.py`(도구 목록에 law_route)

**Interfaces:**
- Consumes: `engine.follow.follow(store, doc_id, path, region_doc_id, direction, depth) -> FollowResult`(`refs: list[Ref]` — `Ref.law/.article/.path/.relation/.doc_id`, `more: list[dict]`), `tools.article.resolve_doc`, `tools.article.resolve_region`, `engine.resolve.parse_article`.
- Produces:
  - `engine.route.route(store, doc_id: int, path: str, region_doc_id: int | None, depth: int, steps_max: int, must_relations: set[str]) -> dict` → `{"단계": [{"law": str, "article": str, "관계": str, "꼭_열기": bool, "이어진": int | None}], "멈춤": [dict]}` (`이어진` = 앞 단계 번호(0부터), 시작은 None).
  - MCP 도구 `law_route(law: str, article: str, region: str | None = None) -> dict` — `result("ok", {"단계": [...], "멈춤": [...]}, message)`. 법령·지역 해석 실패는 `law_article` 과 같은 실패/되묻기 봉투.
  - 설정(`config.py`): `route_depth: int = 3`, `route_steps_max: int = 12`, `route_must_relations: str = "시작,상위,하위,조례,근거조문"` (follow 가 내는 관계 이름 — 쉼표 구분).

'꼭 열기' 판정: 관계가 `route_must_relations` 에 있거나, **시작 조문에서 바로(깊이 1) 다른 문서로 가는 인용**이면 참. 같은 문서 안의 단순 인용(정의 조문 등)과 깊이 2 이상의 단순 인용은 거짓. 방향은 `"both"`. follow 의 `more`(지역 없어 조례로 못 내려감, 연결이 많아 생략)는 `멈춤` 으로 옮긴다.

- [ ] **Step 1: 실패하는 시험** (`test_route.py`, conftest 의 `store` 사용)
  - `test_route_from_building_coverage_reaches_national_planning_act`: 건축법 `제55조`, 지역 없음 → 단계에 `("국토의 계획 및 이용에 관한 법률", 제77조로 시작)` 이 있고 `꼭_열기` 참.
  - `test_route_without_region_reports_where_it_stopped`: 같은 입력 → `멈춤` 이 비지 않음.
  - `test_route_with_region_reaches_ordinance`: 국토계획법 시행령 `제84조`, 지역 영천시 → `영천시 도시계획 조례` 단계가 있고 꼭_열기 참.
  - `test_route_same_doc_definition_cite_is_not_must`: 결과 중 시작과 같은 문서이고 관계가 `인용` 인 단계는 모두 꼭_열기 거짓.
  - `test_route_caps_steps`: `steps_max=3` → 단계 3개 이하, 첫 단계 관계 `시작`.
  - `test_law_route_tool_unknown_law_fails_like_law_article`: 없는 법령 → `status == "failed"`.
- [ ] **Step 2:** `uv run pytest -q tests/test_route.py` → FAIL(모듈 없음).
- [ ] **Step 3:** 구현. article 문자열 → path 는 `law_article` 과 같은 해석(`parse_article`)을 쓴다. 단계의 `article` 은 `law_article` 에 그대로 넣을 수 있는 꼴(follow `Ref.article`).
- [ ] **Step 4:** 위 시험 + 전체 MCP 시험 PASS.
- [ ] **Step 5:** 커밋 `feat(route): 조문 하나에서 인용·위임 연결을 따라 열 순서와 꼭 열 조문을 계산하는 law_route`.

### Task 3: 공용 runtime — 설정 디렉터리의 `query.py` 로 질의 처리 바꾸기

search_plan 은 모델 루프가 아니라 정해진 순서라, `actions.py` 와 같은 방식으로 `AGENT_DIR/query.py` 의 `run_query` 가 있으면 그것을 쓴다.

**Files:**
- Modify: `agent/runtime/src/executor.py` (`Executor.__init__` 기본 경로)
- Test: `agent/runtime/tests/test_query_override.py`

**Interfaces:**
- Produces: `executor.load_agent_query(agent_dir: Path) -> Callable | None` — `agent_dir/"query.py"` 가 있으면 그 모듈의 `run_query`, 없으면 None. `Executor(store)` 는 주입이 없을 때 `load_agent_query(AGENT_DIR) or loop.run_query` 를 쓴다.

- [ ] **Step 1: 실패하는 시험** — `test_query_py_in_agent_dir_replaces_the_loop`(tmp 폴더에 `async def run_query(query, **kw): return {"answer": "x", "tool_results": {}, "stopped": "answer", "usage": {}}` 를 쓴 `query.py` → `load_agent_query(tmp)` 가 그 함수), `test_no_query_py_keeps_the_loop`(→ None).
- [ ] **Step 2:** `cd agent/runtime; unset VIRTUAL_ENV; uv run pytest -q tests/test_query_override.py` → FAIL.
- [ ] **Step 3:** 구현(`shape._load_agent_actions` 와 같은 importlib 방식).
- [ ] **Step 4:** PASS + 런타임 전체 PASS.
- [ ] **Step 5:** 커밋 `feat(runtime): 설정 디렉터리에 query.py 가 있으면 그 run_query 로 질의를 처리`.

### Task 4: `agent/search_plan` — 계획 만들기

**Files:**
- Create: `agent/search_plan/query.py`, `agent/search_plan/agent.yaml`, `agent/search_plan/prompt.txt`(빈 파일 가능 — runtime 이 읽음), `agent/search_plan/select_prompt.txt`, `agent/search_plan/.env.example`
- Test: `agent/runtime/tests/test_search_plan.py` (`query.py` 를 importlib 로 경로 로드)

**Interfaces:**
- Consumes: `loop.McpAdapter().call(tool, args)`, `loop.ModelAdapter().next_step(messages, [], require=False) -> {"answer": str, "usage": {...}}`, law-mcp `law_search`(facts `결과[].호출인자 {law, article}`), `parcel_zoning`(facts `시도`, `시군구`), Task 2 `law_route`.
- Produces: `query.run_query(query: str, *, mcp=None, model=None, on_progress=None, prior=None, context=None) -> dict` → `{"answer": "", "tool_results": {"search_plan": {"status": "ok", "facts": {PLAN_KEY: plan}}}, "stopped": "answer", "usage": {...}}`. `plan = {"단계": [...Task 2 단계 꼴, 단계마다 "시작" 번호 덧붙임...], "멈춤": [...], "지역": str | None}`. 빈 계획 = `{"단계": [], "멈춤": [], "지역": None}`.
- 설정(`agent/search_plan/.env.example`, 코드의 기본값은 빈 값/일반값): `PLAN_KEY=검색_계획`, `PLAN_SEARCH_TOOL=law_search`, `PLAN_SEARCH_LIMIT=20`, `PLAN_ROUTE_TOOL=law_route`, `PLAN_PARCEL_TOOL=parcel_zoning`, `PLAN_REGION_KEYS=시도,시군구`, `PLAN_SELECT_PROMPT=select_prompt.txt`, `PLAN_START_MAX=3`, `PLAN_STEPS_MAX=16`, `MCP_SERVERS={"law":"http://localhost:7017/mcp?caller=search_plan"}`, `PORT=7018`, `VLLM_*` 는 비움.
- 고르기 응답 JSON: `{"시작": [후보 번호...], "주소": str | null}`. 번호는 프롬프트에 매긴 후보 순번(0부터). 키 이름도 `.env`(`PLAN_PICK_KEY=시작`, `PLAN_ADDRESS_KEY=주소`).

순서: ① `law_search(query, limit)` → 후보(중복 (law, article) 제거) ② 후보 목록을 번호 매겨 select_prompt 와 함께 모델 1회 ③ 응답 JSON 의 번호 중 후보 범위 안 것만, `PLAN_START_MAX` 까지 ④ 주소가 있으면 `parcel_zoning(주소)` → `PLAN_REGION_KEYS` 값을 공백으로 이어 지역; 실패면 지역 None ⑤ 시작마다 `law_route(law, article, region)` → 단계 이어붙이기((law, article) 중복 제거, `PLAN_STEPS_MAX`). 어느 단계든 예외·실패는 빈 계획(①②③) 또는 그 시작만 건너뜀(⑤).

- [ ] **Step 1: 실패하는 시험** (FakeMcp: 도구별 고정 결과 기록, FakeModel: 고정 answer)
  - `test_plan_follows_search_pick_and_route`: 후보 2개, 모델 `{"시작":[1],"주소":null}` → law_route 가 후보 1 의 호출인자로 한 번 불리고, facts[PLAN_KEY].단계 가 law_route 단계와 같음.
  - `test_address_is_resolved_to_region_before_routing`: 모델 `{"시작":[0],"주소":"영천시 금호읍 신월리 1-51"}`, parcel 결과 facts `{"시도":"경상북도","시군구":"영천시"}` → law_route 인자 `region == "경상북도 영천시"`, plan.지역 같음.
  - `test_selection_outside_candidates_gives_empty_plan`: `{"시작":[7]}` → 단계 [], law_route 안 불림.
  - `test_broken_json_gives_empty_plan`: answer `"모르겠다"` → 빈 계획, stopped "answer".
  - `test_search_failure_gives_empty_plan`: law_search 예외 → 빈 계획.
  - `test_duplicate_steps_are_merged`: 두 시작의 경로가 같은 조문을 내면 한 번만.
- [ ] **Step 2:** `uv run pytest -q tests/test_search_plan.py` → FAIL.
- [ ] **Step 3:** 구현 + `select_prompt.txt`(후보 중 질문에 답하는 데 출발점이 되는 조문 번호를 고르고, 질문에 필지 주소가 있으면 그대로 옮겨 적으라는 지시. JSON 만. 조문·법령 예시는 넣지 않는다) + `agent.yaml`(law_review 의 것을 본떠 스킬 1개 "검색 계획").
- [ ] **Step 4:** PASS + 런타임 전체 PASS.
- [ ] **Step 5:** 커밋 `feat(search_plan): 검색→LLM 고르기→필지 지역→law_route 로 검색 계획을 만드는 에이전트`.

### Task 5: law_review 런타임 — 계획 받기·붙이기·준수 점검

**Files:**
- Create: `agent/runtime/src/plan.py`
- Modify: `agent/runtime/src/loop.py` (`run_query`: 메시지 만든 뒤·확인할 말 붙인 뒤 계획; 답 점검 블록에 미준수), `agent/runtime/src/config.py`
- Modify: `agent/law_review/.env.example` (새 키, 값은 비우거나 문장만)
- Test: `agent/runtime/tests/test_search_plan_guard.py`

**Interfaces:**
- Produces (`plan.py`):
  - `async def fetch_plan(url: str, question: str, timeout: float) -> dict | None` — `POST {url}/message:send`(run_eval.call 과 같은 A2A 본문, `configuration.blocking` true), 응답 task 의 `facts` 아티팩트에서 `settings.plan_key` 값. 실패·시간 초과·빈 단계 → None.
  - `def plan_text(plan: dict) -> str` — `settings.plan_intro_text` + 단계마다 `settings.plan_step_format.format(n=, law=, article=, relation=, must=settings.plan_must_text if 꼭_열기 else "")` + `멈춤` 이 있으면 `settings.plan_stop_text.format(stops=...)`.
  - `def unopened(plan: dict, messages: list[dict], pointers: set[str]) -> list[str]` — 꼭_열기 단계 중, 어떤 tool 메시지(pointers 칸 제외 — `loop._without_keys`, 공백 제거)에도 **법령명(공백 제거)** 과 **조문 번호(`settings.answer_cite_pattern` 으로 article 에서 뽑은 첫 번호, 공백 제거)** 가 함께 나오지 않는 것들의 `"{law} {article}"`.
- `run_query(..., planner=None)` 인자 추가: None 이고 `settings.plan_agent_url` 이 있으면 `fetch_plan` 을 쓴다. 테스트는 `planner=async fn(question)->dict|None` 주입.
- 설정(`config.py`, 기본값 ""/0): `plan_agent_url`, `plan_timeout_sec: float = 60`, `plan_key: str = "검색_계획"`, `plan_intro_text`, `plan_step_format`, `plan_must_text`, `plan_stop_text`, `plan_retry_text`(`{steps}`), `progress_plan_text`.
- 동작: 재개(carried)·MCP 불통이 아니면 계획을 받아 `messages[-1]` 뒤에 `plan_text` 를 붙이고 `tool_results[settings.plan_key] = {"facts": {settings.plan_key: plan}}` 로 남긴다(성형이 facts 로 올림). 답 점검에서 `missed = unopened(...)` 가 있으면 기존 한 번 되돌림(`cite_checked`)에 `plan_retry_text` 를 더하고 `cite_lookup_due = True`. 되돌림 뒤에도 남으면 `ungrounded` 에는 넣지 않고 `tool_results[plan_key]["facts"]["계획_미준수"] = missed` 로만 남긴다(답의 인용이 아니므로 꼬리표를 붙이지 않는다).

- [ ] **Step 1: 실패하는 시험** (기존 `test_lookup_guard.py` 의 FakeMcp/FakeModel 방식)
  - `test_plan_is_appended_to_the_question`: planner 가 단계 1개 계획 → 첫 모델 호출의 마지막 user 메시지에 그 법령·조문이 있음.
  - `test_unopened_must_step_is_sent_back`: 모델이 law_article 을 안 부르고 답 → 두 번째 답 전에 `plan_retry_text` 문장이 user 메시지로 들어감, 그 라운드 `require=True`.
  - `test_step_seen_in_chain_excerpt_counts_as_opened`: 도구 결과 refs 발췌에 그 법령·조문이 있으면 되돌리지 않음.
  - `test_step_only_in_pointer_list_is_not_opened`: pointer 칸에만 있으면 되돌림.
  - `test_plan_agent_down_answers_as_before`: planner 가 None → 메시지·흐름이 planner 없을 때와 같음.
  - `test_no_plan_url_never_calls_planner`: 설정 비고 planner 없음 → fetch_plan 안 불림(monkeypatch 로 호출 시 실패).
  - `test_resume_does_not_fetch_plan_again`: prior 메시지가 있으면 planner 안 불림.
  - `test_plan_is_reported_in_facts_with_misses`: 되돌림 뒤에도 안 열면 결과 tool_results[plan_key].facts.계획_미준수 에 그 조문.
  - `fetch_plan` 단위: 로컬 FastAPI 없이 `httpx.MockTransport` 로 facts 아티팩트 → 계획, 500 → None.
- [ ] **Step 2:** `uv run pytest -q tests/test_search_plan_guard.py` → FAIL.
- [ ] **Step 3:** 구현. `.env.example`(law_review)에 `PLAN_AGENT_URL=`(빈 값), 문장 키는 한국어 문장으로.
- [ ] **Step 4:** PASS + 런타임 전체 PASS(442 + 새 시험).
- [ ] **Step 5:** 커밋 `feat(plan): 질문마다 검색 계획을 받아 붙이고 꼭 열 조문을 안 열었으면 되돌림(PLAN_AGENT_URL 비면 그대로)`.

### Task 6: 띄우기 스크립트 — search_plan(7018)·작은 모델 law_review

**Files:**
- Modify: `agent/scripts/run.sh`
- Modify: `agent/law_review/.env`(로컬, 커밋 안 함): `PLAN_AGENT_URL=http://localhost:7018`
- Create: `agent/search_plan/.env`(로컬, 커밋 안 함): `.env.example` 복사 + `VLLM_*`

**Interfaces:**
- `run.sh start|stop|status` 가 search_plan 도 띄움(`AGENT_DIR=agent/search_plan`, pid `logs/search_plan.pid`, 로그 `logs/search_plan.log`).
- `run.sh start-small <model> <port>` — law_review 를 `PORT=<port> VLLM_BASE=http://localhost:9001/v1 VLLM_MODEL=<model> VLLM_API_KEY=ollama` 환경으로 하나 더 띄움(pydantic 환경변수가 `.env` 를 이김). `PLAN=off` 를 주면 `PLAN_AGENT_URL=` 로 띄움. search_plan 도 같은 모델로: `start-small-plan <model> <port>`(기본 7028) 를 두고, 그 URL 을 그 law_review 의 `PLAN_AGENT_URL` 로.
- ollama 는 `OLLAMA_CONTEXT_LENGTH=32768` 로 다시 띄워야 한다(기본 문맥이 짧아 시스템 문장이 잘림) — 스크립트 주석과 Task 8 에 적는다.

- [ ] **Step 1:** 구현 후 `agent/scripts/run.sh stop; agent/scripts/run.sh start; agent/scripts/run.sh status` → 7016·7017·7018 모두 응답.
- [ ] **Step 2:** `curl -s localhost:7018/.well-known/agent-card.json` 에 스킬 1개.
- [ ] **Step 3:** 수동 확인 — 7016 에 "건폐율 기준이 뭐야?" → `logs/law_review.log` 에 계획을 받은 기록, 결과 facts 에 `검색_계획`.
- [ ] **Step 4:** 커밋 `chore(run): search_plan(7018)과 작은 모델 law_review 띄우기`.

### Task 7: 평가 — 호출자 거르기·새 지표·세 조건 비교

**Files:**
- Modify: `eval/run_eval.py`
- Create: `eval/rand100.yaml` (작업 폴더 `/tmp/claude-0/-home-ubuntu-permit-law/8035921b-f8ec-406d-a41a-296426996858/scratchpad/rand100.yaml` 복사)
- Test: `eval/test_run_eval.py` (없으면 생성; `cd eval; uv run --project ../mcp pytest -q test_run_eval.py` 또는 python3 로 돌 수 있게 순수 함수만)

**Interfaces:**
- `--ignore-caller search_plan`(기본값 search_plan): 추적 행 `caller` 가 그 값이면 버림.
- `evaluate()` 결과에 추가: `"grounded_ok": bool`(verification 아티팩트의 `unverified` 가 비었거나 아티팩트 없음), `"plan_steps": int`, `"plan_must": int`, `"plan_missed": list[str]`(facts `검색_계획`·`계획_미준수` — 키는 `--plan-key`, 기본 `검색_계획`).
- 요약에 `grounded_rate`, `plan_follow_rate`(=1 - missed/must 합, must 0 이면 None).

- [ ] **Step 1: 실패하는 시험** — `test_rows_from_ignored_caller_are_dropped`, `test_grounded_ok_reads_verification`, `test_plan_follow_rate`.
- [ ] **Step 2:** FAIL 확인 → **Step 3:** 구현 → **Step 4:** PASS.
- [ ] **Step 5:** 커밋 `feat(eval): search_plan 호출 거르기·근거 통과율·계획 준수율`.

### Task 8: 실험 — ① 120b 계획 없음 ② 작은 모델 계획 없음 ③ 작은 모델(+120b) 계획 있음

코드 변경 없음. 결과를 `docs/notes/2026-11-16-search-plan-results.md` 로 남긴다.

- [ ] **Step 1:** ollama 를 `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` 로 다시 띄움(기존 프로세스 종료 후). `ollama ps` 로 확인.
- [ ] **Step 2:** 세트: `eval/golden.yaml` 과 `eval/rand100.yaml`. 먼저 `--only` 로 golden 10문항 스모크(조건마다), 끝나면 전체.
- [ ] **Step 3:** 조건별 실행 — ① `--agent http://localhost:7016`(PLAN 끔) ② `start-small` PLAN=off, 모델 3종 ③ `start-small-plan` 모델 3종 + 120b 계획 켬. 각 결과 JSON 경로 기록.
- [ ] **Step 4:** 표로 정리: 모델×조건별 `grounded_rate` · `recall_avg` · `plan_follow_rate` · `halluc_cases` · 평균 초. 성공 판정(spec §7): ③>② 뚜렷, ③이 ①에 가까움, 환각 ③≤②, ③-120b ≥ ①.
- [ ] **Step 5:** 문서 커밋 `docs(notes): 검색 계획 에이전트 실험 결과`. 문제점(계획이 틀린 문항, 시작 조문 고르기 실패 문항)을 따로 적는다.

---

## Rulings (계획 작성 때 정한 것)

- **building_basis 와 함께 쓰기:** spec 은 "계획은 building_basis 를 부를 단계로 두고"라고 했지만, law_review 는 이미 질문 정리 단계(`RESTATE_LEAD_TOOLS`)가 건축 질문에서 building_basis 를 먼저 부르게 한다. 계획에 도구 단계를 따로 넣으면 같은 호출이 두 번 생긴다. 그래서 건축 흐름은 그대로 두고, 계획은 검색으로 고른 시작 조문에서 계산한 단계만 준다. building_basis 가 표시하는 핵심 조문은 지금의 pointer 되돌림이 계속 맡는다. 틀렸을 때 비용: 건축 질문에서 계획이 building_basis 결과와 겹치거나 빈약해짐 — Task 8 에서 건축 문항을 따로 보고 판단.
- **search_plan 의 law-mcp 호출 구분:** MCP URL 쿼리 `?caller=` 로 표시(Task 1). 별도 추적 파일보다 손이 적고 평가만 고치면 된다.
