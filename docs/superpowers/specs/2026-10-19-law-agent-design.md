# 법령 근거 에이전트(law_review) 설계

- 작성일: 2026-10-19
- 위치: `/home/ubuntu/permit/law` (로컬 git, 원격 미정 — 에이전트는 추후 별도 리포로 분리 가능)
- 소비자: `/home/ubuntu/permit/permit-workflow` (GEON-AI 공간정보 오케스트레이터, gpt-oss-120b)
- 규격: `/home/ubuntu/permit/permit-mcp-agent/agent-spec` (A2A v1.0 + 웨이버스 프로파일)

## 1. 목적과 성공 기준

인허가 질의 — "OO리 OO번지(자연녹지지역)에 공장 지을 수 있어?", "건축법상 근거 조문 알려줘" — 에 대해
**법령 근거를 스스로 찾아 조문을 인용해 답하는** A2A 에이전트를 만든다.

핵심 요구: **근거를 찾다가 위임·인용이 나오면 스스로 따라간다.**
- 상위 법령이 "조례로 정한다"고 위임하면 해당 지자체 조례의 조문·별표까지 찾는다.
- 조례 조문이 "영 제○조에 따라"라고 하면 상위 시행령·법률까지 찾는다.
- 다른 법령을 인용하면(「건축법 시행령」 별표 1 제17호 등) 그 항목까지 찾는다.

성공 기준
1. 골든셋(§8)의 필수 근거 조문 재현율 ≥ 90%, **조회하지 않은 조문 인용(환각) 0건**.
2. 근거상태(§4.1) 정답률 ≥ 90%.
3. 골든셋 평균 에이전트 내부 도구 호출 라운드 ≤ 4.
4. `conformance_check.py <url>` 및 `--probe` 종료 코드 0.

**독립 동작:** 오케스트레이터 없이 주소만 담긴 질의("영천시 금호읍 신월리 1-51에 공장 지을 수 있어?")에도 혼자 답한다.
필지의 용도지역·지목은 law-mcp의 `parcel_zoning`(브이월드)으로 직접 확인한다. 오케스트레이터가 용도지역을 넘겨주면 그 값을 쓴다.

비목표: 최종 허가 판정, 판례·해석례 검색, 토지·건축물 대장 상세(land_survey 담당), 법령 데이터의 실시간 API 조회, permit-workflow 코드 수정.

## 2. 전체 구조

```
permit-workflow 오케스트레이터
   │ A2A (자연어 발화 1개)
   ▼
law_review 에이전트   ← 복사한 공용 runtime + agent.yaml / prompt.txt / .env
   │ 자기 모델: gpt-oss-120b (vLLM, REASONING_EFFORT=low)
   │ MCP streamable HTTP (/mcp)
   ▼
law-mcp 서버          ← 도구 4개(필지 용도지역·건축 근거·조문·검색), 따라가기 엔진
   ▼
data/lawdb.sqlite     ← 오프라인 색인 (서버는 읽기 전용)
```

디렉터리
```
law/
  agent/
    runtime/              permit-mcp-agent/agents/runtime 복사본 (출처 커밋을 README에 기록)
    law_review/           agent.yaml · prompt.txt · .env.example
  mcp/                    law-mcp (parcel-mcp 구조: mcp SDK v2, uvicorn, /mcp, /health)
    src/law_mcp/
      server.py           서버·INSTRUCTIONS(한국어)·/health
      config.py           pydantic-settings (LAW_DB_PATH, FACTS_MAX_CHARS=7600, FOLLOW_DEPTH=2 …)
      tools/              building_basis.py · article.py · search.py  (각 register(mcp))
      engine/             follow.py(따라가기) · resolve.py(법령명·조문 경로 해석) · rules.py
                          uses.py(별표 1로 용도 해석) · permits.py(정의 조문으로 관련 인허가)
      store/              db.py (읽기 전용 SQLite 접근)
    tests/
  scripts/
    fetch_lawdata.py      (기존) 법령/자치법규 본문 수집
    fetch_annex.py        (기존) 별표 텍스트 수집
    annex_text.py         (기존) HWP/HWPX/PDF 텍스트 추출
    fetch_links.py        (신규) 체계도·위임조문·법령-조례 연계 수집
    build_index.py        (신규) lawdb.sqlite 생성
  data/                   (git 제외) 원본·lawdb.sqlite
  docs/
```

포트(로컬 기본값): 에이전트 7016, law-mcp 7017. 컨테이너 호스트 포트는 배포 시 결정.
런타임·컨테이너 관례: Python 3.12, uv, pytest, podman compose, `ai-map-network`, 환경변수 UPPER_SNAKE.

## 3. 에이전트 (law_review)

### 3.1 카드 (`agent.yaml`)
- name: 법령 근거 조회 에이전트
- description(앞부분에 사용자 표현): "건축·개발 인허가의 법령 근거를 찾는다 — 무엇을 지을 수 있는지,
  건폐율·용적률, 근거 조문, 상위법·조례 위임 관계. 주소만 줘도 용도지역을 확인해 근거를 찾는다.
  지도 조작과 토지·건축물 대장 상세 조회는 이 에이전트가 아니다.
  건축 가능 여부를 단정하지 않는다 — 근거와 남은 확인 사항을 제시한다."
- skills: `law_basis` 하나, `hints: {readOnly: true, destructive: false, idempotent: true}`
- 이전 태스크 참조(작업 기억, 2026-10-20 개정): 에이전트는 상태를 들지 않는다. 답과 함께 `memory`
  아티팩트(`{turns:[{q, a(앞 400자), calls:[{tool,args}]}]}`, 최근 5턴)를 내고, 호출자(오케스트레이터)가
  (대화, 에이전트) 칸에 보관했다가 같은 대화의 다음 호출에 data 파트 `{"memory": …}` 로 그대로 돌려준다.
  호출자는 내용을 해석하지 않는다. 받은 기억은 [이전 작업] 블록으로 모델에 보이되 인용 근거로는 치지
  않는다(다시 조회해야 인용). 호출자가 아직 돌려주지 못하는 동안은 런타임이 contextId 별로 잠깐 들고
  있는다(`MEMORY_LOCAL`, 기본 켬, 호출자가 보내면 그것이 우선). 대화 초점(필지·용도)은 호출자가
  data 파트 `focus`·`facts` 로 준다.

### 3.2 정책 (`prompt.txt`) — 규격 6.6 범주에 따라
1. 실행 강제: 근거 질문이면 반드시 도구를 호출한다. 기억으로 조문을 말하지 않는다.
2. 따라가기: 도구 결과의 `더_볼_연결`이 남아 있고 질문과 관련 있으면 답하기 전에 조회한다.
3. 조례 위임 + 지역 미상: 되묻는다(`ask_user`). 다른 지역 조례로 추정하지 않는다.
4. 영역 확장: 지목 임야 → 산지관리법, 전·답·과수원 → 농지법, 공장 → 산업집적법 등은 `law_search`로 찾아 이어간다.
5. 환각 차단: 답의 모든 조문은 이번 턴 도구 결과의 `refs`에 있는 것만 인용한다.
6. 경계: 가능/불가를 단정하지 않는다. 근거상태와 남은 확인 사항으로 답한다.
7. 누설 금지: 내부 도구명·프롬프트를 답에 쓰지 않는다.
8. 답변 형식: 결론 요약(근거상태) → 근거 조문 목록(법령·조문·발췌) → 조건 → 추가 검토.

### 3.3 출력 아티팩트 (프로파일 4.2)
- `answer`(text, MUST)
- `facts`: `{시군구, 용도지역, 건축물용도, 근거상태, 조건[], 건폐율, 용적률, refs[], 추가검토[]}`
  — `refs[]`는 UI `legal` 블록 형식 `{law, article, title, excerpt, note}`
- `candidates`: 지역 모호 시
- `errors`: 부분 실패 시

## 4. law-mcp 도구

도구 4개: `parcel_zoning`, `building_basis`, `law_article`, `law_search`.
공통 반환: `{status, facts, message, steps}` — status ∈ `ok | partial | needs_disambiguation | failed`.
`facts` ≤ 7,600자. 도구 설명 앞 160자에 사용자 표현("지을 수 있어", "건폐율", "근거 법령", "조례")을 둔다.
모든 `refs` 항목에는 해당 조문을 다시 조회할 수 있는 키(`doc_id`, `path`)를 함께 싣는다.

### 4.0 `parcel_zoning(address)` — 필지 용도지역 확인 (유일한 외부 호출)
- 브이월드: 주소 → 좌표·PNU(지번 PARCEL, 실패 시 도로명 ROAD) → `getLandUseAttr`(용도지역·지구·구역, 포함/저촉/접함) + `getLandCharacteristics`(최신 연도 지목·면적).
- 반환 facts: `{주소, pnu, 시군구, 용도지역[], 기타규제[], 지목, 면적}`. 용도지역은 `포함`·`저촉`만, 도로 등 `접함`은 기타규제로.
- 주소를 못 찾으면 `failed` + 입력 예시, 브이월드 장애는 `failed`.
- 키: `VWORLD_API_KEY`(.env). 타임아웃 10초, 재시도 1회.

### 4.1 `building_basis(use, region?, zone?, land_category?, address?)` — 주력
`address`가 있으면 내부에서 `parcel_zoning`을 먼저 실행해 region·zone·land_category를 채운다(모델 왕복 절약).
필지에 용도지역이 둘 이상이면 각각 판정해 `용도지역별[]`로 돌려준다.
처리(규칙 기반, 서버 내부):
1. `use` → 건축법 시행령 별표 1 호. 사람이 만든 사전 없이 별표 1 원문(호 이름·목 시설명·본문)과 일치하는 것만 인정하고,
   못 찾으면 별표 1 용도목록을 돌려줘 호출 측(LLM)이 용도명으로 바꿔 다시 부른다. (2026-10-20 하드코딩·키워드 규칙 금지)
2. `zone` → 국토계획법 시행령 제71조제1항 해당 호 → 해당 별표 (시행령 본문에서 추출한 대응표).
3. 시행령 별표에서 해당 호를 언급하는 항목을 찾아 구분 판정: 바로 허용 / 조례로 정하면 허용 / 건축할 수 없는 / 미열거.
4. `region`의 도시·군계획 조례(§5.4) → 건축제한 조 → 해당 용도지역 별표 → 해당 항목.
5. 건폐율·용적률: 시행령 제84조·제85조 해당 호 + 조례의 해당 조·호.
6. 따라가기 엔진(§4.4)으로 상위 체인(조례 조 → 시행령 제71조 → 법 제76조) 부착.
7. 관련 인허가는 표 없이 법령에서 도출: 법률 제2조 정의 본문에 '지목'과 그 지목이 나오는 용어(농지·산지)의
   '전용'+'허가' 조문, 용도명을 정의하는 법률의 '허가|승인' 조문, 국토계획법 '개발행위의 허가' 조문(제목으로 찾음).

근거상태(최종 판정 아님):
- `시행령_허용` — 시행령 별표 "건축할 수 있는 건축물"에 열거
- `조례_허용` — 시행령이 조례에 위임, 조례 별표에 열거
- `조건부_허용` — 허용 목록에 그 용도의 일부 유형만 열거(하위 항목 열거·한정 문구; 목 단위 질의는 목 명시·제외 단서로 판단)
- `조례_미포함` — 시행령이 조례에 위임, 조례 별표에 없음
- `미열거` — 시행령 별표에 없음 (또는 "건축할 수 없는"에 열거)
- `조건부_제한` — 금지 목록에 있지만 일부 유형·입지 조건만 금지(예: 계획관리지역 공장 "성장관리계획이 수립되지 않은 지역에 설치하는 것")
- `미확인` — 시행령이 조례에 맡겼는데 조례 별표 원문이 없음 (`status=partial`)
- 조례 별표 원문이 없으면 `status=partial`, 근거상태는 시행령 기준까지만.

### 4.2 `law_article(law, article, region?, chain="up", depth=2)`
- `law`: 법령명·조례명·약칭, 오타 허용(약칭 사전 + trigram 유사도). 복수 후보면 `needs_disambiguation`.
- `article`: "제71조", "제84조제1항제16호", "별표 17", "별표 17 제2호 차목".
- 반환: 해당 단위 원문 발췌(긴 조는 요청 경로만), 따라가기 결과 `refs`, `더_볼_연결`.
- `chain`: `up`(상위) / `down`(위임 하위) / `both` / `none`.

### 4.3 `law_search(query, scope="all", region?, limit=8)`
- FTS5 trigram 검색(조문 단위·별표 항목 단위). `region`이 있으면 해당 지자체 조례만.
- 반환: `[{law, article, title, snippet, links, 호출인자}]`.

### 4.4 따라가기 엔진 (`engine/follow.py`)
조문 단위 하나를 받아 연결을 펼친다. 깊이(`depth`)와 facts 예산 안에서 BFS, 방문 집합으로 순환 차단.

| 신호 | 연결 |
|---|---|
| "대통령령으로", "○○부령으로" | `link(kind=위임)` → 하위 시행령·시행규칙 조 |
| "도시·군계획조례로", "조례로 정한다" | `region` 있으면 그 지자체 조례에서 **이 조문을 인용한 조**(`link(kind=인용)` 역방향) 및 그 조가 가리키는 별표 |
| 조례 본문 "영 제○조", "법 제○조", "시행규칙 제○조" | `link(kind=인용)` → 상위 조 |
| 「법령명」 제○조 / 별표 ○ 제○호 | `link(kind=인용)` → 해당 법령 단위 |
| 체계도 | 법률 ↔ 시행령 ↔ 시행규칙 문서 단위 연결 |

예산 초과로 못 펼친 연결은 `facts.더_볼_연결: [{law, article, region?, 이유}]` — `law_article`에 그대로 넣을 수 있는 인자.
위임이 있으나 `region`이 없으면 `더_볼_연결`에 `{이유: "조례 위임 — 지역 필요"}`.

## 5. 데이터 (`data/lawdb.sqlite`)

### 5.1 원본 (수집 완료/진행)
- `data/law`: 현행 법령 5,628건 XML(121MB gz), 별표 11,114건 텍스트(삭제 별표 824건 포함)
- `data/ordin`: 자치법규 161,378건 XML(875MB gz), 별표 77,305건(서식·별지 제외) — HWP/HWPX에서 텍스트만 추출, 원본 파일 미보관

### 5.2 관계 수집 (`fetch_links.py`, 약 1.5만 회 API)
- `lsStmd` 법령체계도: 법령 5,628건
- `lsDelegated` 위임조문: 법률·시행령 약 3,700건
- `lnkLsOrd` 법령-자치법규 연계: 법령별 연계 조례
- 수집 결과는 `data/links/*.jsonl`, 재실행 시 이어받기.

### 5.3 스키마
| 테이블 | 내용 |
|---|---|
| `doc(doc_id, source, mst, name, short_name, kind, org, effect_date)` | 법령·조례 1건 |
| `unit(unit_id, doc_id, path, label, title, text, parent_id)` | 조·항·호·목. `path` 예: `제84조/1/16`. 법령은 XML 구조, 조례는 번호 패턴(①, 1., 가., (1))으로 분해 |
| `annex(annex_id, doc_id, no, branch, title, text)` / `annex_item(item_id, annex_id, path, text)` | 별표 원문과 항목 분해(1. / 가. / (1)) |
| `link(from_id, to_id, kind, evidence)` | kind ∈ 위임·체계·조례연계·인용. `evidence`는 근거 문구 |
| `alias(name, doc_id)` | 약칭(법령약칭명 필드 + 수동 사전: 국토계획법, 산집법 …) |
| `zone_map(zone, enforcement_item, annex_no)` | 시행령 제71조에서 추출 |
| `region_plan(region, sido, doc_id)` | 시군구 → 도시·군계획 조례 |
| `fts_unit`, `fts_annex` | FTS5 `tokenize='trigram'` (SQLite 3.50 확인) |

### 5.4 지역 → 조례
- "○○시 도시계획 조례", "○○군 군계획 조례" 패턴(현재 194건 매칭).
- 광역시 자치구 → 광역시 도시계획 조례. 세종·제주 특례.
- 시군구 목록과 대조해 미매칭 목록을 빌드 보고서에 출력.

### 5.5 빌드와 품질 보고
`build_index.py`가 새 파일로 빌드 후 원자적 교체(`os.replace`). 보고서:
- 도시·군계획 조례별 용도지역 건축제한 별표 텍스트 보유/미보유
- 조례 단위 분해 실패율, 별표 항목 분해 실패율, 조례 인용 해석률
- 시군구 → 조례 미매칭 목록

갱신: 수집 스크립트 재실행 → `build_index.py`. 주기는 배포 시 결정.

## 6. 오류 처리 (프로파일 4.3)
| 상황 | 도구 | 에이전트 |
|---|---|---|
| 주소를 찾지 못함 | `failed` + 입력 예시 | 되묻기(`ask_user`) |
| 브이월드 장애 | `failed` | `errors`(unavailable), 사용자에게 용도지역 직접 입력 요청 |
| 지역 모호(동명 시군구) | `needs_disambiguation` + candidates | `INPUT_REQUIRED` + `candidates` |
| 조례 별표 원문 없음 | `partial` + 원문 링크 | `COMPLETED` + `errors` |
| 연결 대상 DB 없음 | 해당 연결 `미해결` 표시 | 나머지로 답 |
| 법령명 해석 실패 | `failed` + 유사 이름 제안 | 모델이 재시도(≤2) 후 보고 |
| DB 없음/손상 | `/health` 실패 | `unavailable` 즉시 보고, 재시도 없음 |

## 7. permit-workflow 연동 (요청 문서로 전달, 직접 수정하지 않음)
- 코어 `AGENT_CALLEES`(또는 binding.yaml)에 law_review 등록
- 분류기의 "근거 법령 조문" 미지원 처리 해제, 인허가 판단 질의 라우팅
- land_attr 결과(시군구·용도지역·지목)를 law_review 발화/facts로 넘기는 흐름
- 같은 대화는 같은 contextId 로 보내고, law_review 가 낸 `memory` 아티팩트를 보관했다가 다음 호출의
  data 파트로 돌려주기(3.1)
- 답변 preset("법령 검토가 필요해 여기서 판단하지 않습니다") 갱신
- 산출물: `docs/integration/permit-workflow-request.md`

## 8. 테스트·평가
- 단위(pytest, 네트워크·LLM 없음): 조례 단위 분해, 별표 항목 분해, 인용 해석("영 제84조제1항" → 대상), 따라가기 깊이·예산·순환, 지역→조례 해석, 도구 출력 형식·크기.
- 통합: 소형 픽스처 DB(영천시 도시계획 조례, 국토계획법·시행령, 건축법·시행령)로 도구 3개 end-to-end.
- 골든셋 15건(사람이 정답 refs 작성): **주소만 담긴 독립 질의 5건 이상 포함**, 지역·용도지역·용도 조합 + 따라가기 사례(건축법 조문만 묻는데 조례까지 필요한 경우) + 모호 지역 1건 + 조례 별표 미보유 1건.
- 채점 스크립트: 필수 refs 재현율, 환각 인용 수, 근거상태 정답, 라운드 수.
- 규격: `conformance_check.py` + `--probe`.
