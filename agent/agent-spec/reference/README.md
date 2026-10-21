# 규격 정본 사본 — 최종 근거

**여기 있는 A2A 파일이 표준이다.** 바깥의 `SPEC.md` 와 어긋나면 여기가 맞다.

`SPEC.md` 와 `conformance_check.py` 의 모든 값이 여기서 나왔다.

| 파일 | 출처 | 지위 |
| --- | --- | --- |
| `a2a.proto` | A2A 규격 리포 `specification/a2a.proto` | **규범.** 타입·enum 의 정본 |
| `specification.md` | A2A 규격 리포 `docs/specification.md` | 설명. 경로·헤더·표현 규칙 |
| `stub_agent.py` | **우리가 직접 작성** | 표준이 아니라 **샘플**. §2 를 지키는 최소 구현이고 점검기 검증에도 쓴다 |

**A2A Protocol v1.0** · Linux Foundation
리포 `https://github.com/a2aproject/A2A`
커밋 `c0f30b35390c59d2cc398a1100823a9115b97a20` (2026-08-31)

두 파일은 원본 그대로다. 고치지 않는다 — 새 버전을 반영하려면 다시 받아 커밋 해시를 이 문서에 갱신한다.

## stub_agent.py

SPEC §2 를 지키는 최소 에이전트다. 표준 라이브러리만 쓴다.

```bash
python3 stub_agent.py 18140                    # 적합한 구현
python3 stub_agent.py 18141 --break-hints      # tool-hints 를 뺀 것 (검사기가 잡아야 한다)
python3 stub_agent.py 18142 --clarify          # 되묻기로 끝나는 것
```

**참조 구현이지 프로덕션 코드가 아니다.** 태스크를 메모리에만 두고, 인증이 없고, 모델도 도구도 부르지 않는다. 경로 B(직접 구현)로 갈 때 "이 정도면 적합하다"를 보는 용도이고, 검사기를 고칠 때 회귀를 잡는 픽스처다.
