# A2A 에이전트 구현 안내 — 배포물

에이전트를 만들려는 사람에게 **이 폴더째로** 건넵니다. **이 묶음만으로 만들 수 있습니다** — 규격·정본·최소 구현·자가 점검기가 다 들어 있고, 언어와 프레임워크는 자유입니다.

## 먼저 알아야 할 것 — 이 묶음은 표준이 아닙니다

표준의 지위를 가진 것은 **A2A Protocol v1.0** 하나이고, 정본 사본이 `reference/` 에 있습니다.

| | 무엇인가 |
| --- | --- |
| `reference/a2a.proto` · `specification.md` | **표준 정본.** 최종 근거는 언제나 이것 |
| `SPEC.md` 2장·3장 | 그 표준을 **옮겨 적은 것.** 틀릴 수 있습니다 |
| `SPEC.md` 6장·8장 | Anthropic 의 도구·에이전트 작성 지침을 옮겨 적은 것 |
| `SPEC.md` 4장 | **우리가 정한 프로파일.** 안 따라도 A2A 적합합니다 |
| `reference/stub_agent.py` | **샘플 구현.** "이 정도면 적합하다" 를 보여 줍니다 |
| `conformance_check.py` | **자가 점검기.** 적합성 보증이 아닙니다 |

## 코딩 에이전트에게 맡긴다면

**폴더째로 주고** 이렇게 시킵니다 — `AGENTS.md` 에 읽는 순서·물어볼 것·점검 방법이 있습니다.

```
agent-spec/ 를 읽고 <무엇을 하는> 에이전트를 만들어줘.
AGENTS.md 부터 읽고 그대로 따라줘.
```

## 구성

```
agent-spec/
  AGENTS.md                    코딩 에이전트용 지시 (사람도 읽으면 좋음)
  SPEC.md                      본문
  conformance_check.py         자가 점검기 (의존성 없음)
  test_conformance_check.py    점검기 자신의 회귀 테스트
  reference/
    a2a.proto                  A2A 정본 — 최종 근거
    specification.md           A2A 설명 문서
    stub_agent.py              2장을 지키는 샘플 구현
    README.md                  정본의 출처와 커밋
```

## 어디부터 읽나

| 상황 | |
| --- | --- |
| **표준만 알고 싶다** | `reference/a2a.proto`, 요약이 필요하면 SPEC 2장·3장 |
| 만든다 | SPEC 1장 → 2장 → 3장 → 5장 → 7장. `reference/stub_agent.py` 가 최소 예입니다 |
| 이 플랫폼에 붙인다 | 위에 더해 SPEC 4장 |
| 잘 돌게 만든다 | SPEC 6장 → 8장 (Anthropic 지침) |
| 남이 만든 것을 검수한다 | SPEC 2장 → 7장 |
| 코딩 에이전트에게 시킨다 | 폴더째로 주고 `AGENTS.md` 를 따르게 합니다 |

## 자가 점검

```bash
python3 conformance_check.py http://호스트:포트
python3 conformance_check.py http://호스트:포트 --probe "<발화>"
```

`--probe` 를 주면 되묻기에 답까지 보내 루프가 닫히는지 봅니다(모델을 부르므로 비용이 듭니다).
종료 코드 **0** A2A 적합 · **1** 부적합 · **2** 점검 불가. **4장 프로파일 미준수는 종료 코드에 영향이 없습니다.**

점검기를 고쳤다면 `python3 test_conformance_check.py` 로 회귀를 봅니다 — 점검기의 상수가 `reference/` 정본과 일치하는지까지 검사합니다.

## 이 문서가 다루지 않는 것

- **도구를 MCP 서버로 만드는 법** — 별도 문서
- **배포별 값** — 포트·모델 주소·MCP 주소
- **오케스트레이터를 만드는 법** — 별도 문서
