"""이 규격이 정의하는 확장의 **공표된 식별자**.

A2A 가 확장을 URI 로 식별하게 한 이유는 **URI 자체가 계약**이기 때문이다. 카드에
이 URI 가 실려 오면 아는 쪽은 읽고 모르는 쪽은 무시한다 — 그것이 `required: false`
의 뜻이고, 사전 합의가 필요 없는 이유다.

**그래서 이 값들은 설정이 아니다.** 설정으로 두면 양쪽이 같은 값을 갖도록 밖에서
맞춰야 하고, 그 순간 확장이 아니라 두 당사자의 사설 관례가 된다 — 제3자가 카드를
읽어도 무엇인지 알 수 없고, 값이 어긋나면 오류 없이 조용히 무시된다.

바꾸면 **다른 확장이 된다.** 기존 구현과 호환되지 않는다.
"""

#: 확장 URI 의 뿌리. 이 규격을 공표하는 주체(웨이버스)가 소유한다. 규격의 일부로 고정한다 —
#: 코어(`ai-map-core/src/callees/a2a.py` 의 EXT_ROOT)와 같은 값이어야 한다.
ROOT = "https://wavus.co.kr/a2a/ext"

#: 스킬별 실행 힌트. 호출자의 스케줄러가 병렬 가능 여부를 판단한다.
TOOL_HINTS = f"{ROOT}/tool-hints/v1"

#: Task.metadata 의 진행률 {fraction, message}.
PROGRESS = f"{ROOT}/progress/v1"

#: Task.metadata 의 토큰 사용량 {tokensIn, tokensOut}.
USAGE = f"{ROOT}/usage/v1"

ALL = (TOOL_HINTS, PROGRESS, USAGE)
