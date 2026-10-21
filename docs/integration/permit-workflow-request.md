# permit-workflow 연동 요청 — law_review(법령 근거 에이전트)

작성 2026-10-20 · 대상: permit-workflow 오케스트레이터(코어) 담당 · 이 저장소는 permit-workflow 를 직접 고치지 않는다.

## 1. 등록
- A2A 카드: `http://<host>:7016/.well-known/agent-card.json` (스킬 `law_basis`, readOnly)
- 코어 `AGENT_CALLEES`(또는 binding.yaml)에 law_review 등록
- 라우팅: 근거 법령·조문·조례, "왜 그런가", 용어 뜻, 인허가 법령 일반 질의(예: AIPS 테스트 85·86·87·19·79·82~84번)

## 2. 호출 계약
| 무엇 | 어떻게 | 필수 |
|---|---|---|
| 사용자 발화 | text 파트에 **원문 그대로** (다시 쓰지 않는다 — 법률 용어가 바뀌면 검색이 엇나간다) | 필수 |
| 대화 묶음 | 같은 대화의 호출은 같은 `contextId` | 필수 |
| 작업 기억 | 직전 호출이 낸 `memory` 아티팩트(data)를 (contextId, law_review) 칸에 저장 → 다음 호출에 data 파트 `{"memory": <그대로>}` | 권장 |
| 대화 초점 | data 파트 `{"focus": "…", "facts": {"주소": …, "pnu": …, "용도": …}}` — 지도에서 고른 필지 등 | 권장 |

- `memory` 는 해석하지 않고 저장·반환만 한다. 크기는 수 KB(최근 5턴, 답 앞 400자, 호출 인자).
- 보내지 않아도 동작한다: law_review 가 contextId 별로 잠깐 들고 있는 대비책이 있다(프로세스 재시작 시 사라짐).
  코어가 반환을 지원하면 law_review 쪽 `MEMORY_LOCAL=false` 로 끈다.

## 3. 응답
- `answer`(text): 사용자에게 보일 마크다운 답 — 근거 조문 표 포함
- `facts`(data): 근거상태, refs 등 구조화 값
- `memory`(data): 위 2절 — 코어가 보관할 것
- 상태: `COMPLETED` / `INPUT_REQUIRED`(되묻기, candidates 있을 수 있음) / `FAILED`(사유 문장)

## 4. 그 밖
- 답변 preset("법령 검토가 필요해 여기서 판단하지 않습니다") 갱신 — 법령 질의는 law_review 로 넘긴다
- law_review 는 가능/불가를 단정하지 않는다(근거상태와 남은 확인 사항). 판정 카드는 코어의 사전진단이 맡는다.
