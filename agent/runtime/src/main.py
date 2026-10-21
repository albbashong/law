"""에이전트 프로세스 진입점.

`AGENT_DIR` 이 가리키는 설정 디렉터리 하나가 이 프로세스를 무슨 에이전트로 만들지
정한다. 런타임 코드는 한 벌이고 도메인을 모른다.

카드와 라우트를 **같은 값에서** 만든다(SPEC §5.3) — `build_app` 이 `streaming` 을
받아 카드의 선언과 라우트의 존재를 함께 정하므로 둘이 갈라질 수 없다.
"""

from __future__ import annotations

import logging

from a2a.card import CardError
from a2a.server import build_app
from a2a.store import TaskStore
from config import CARD_PATH, public_base, settings
from executor import Executor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [" + settings.agent_name + "] %(message)s",
)
log = logging.getLogger(__name__)

#: 이 런타임이 실제로 구현한 것. 카드의 선언이 여기서 나온다.
STREAMING = True
PUSH_NOTIFICATIONS = True

store = TaskStore(ttl_sec=settings.task_ttl_sec, max_tasks=settings.max_tasks)
executor = Executor(store)

try:
    app = build_app(
        store=store, executor=executor, card_path=CARD_PATH,
        url=public_base(),
        streaming=STREAMING, push_notifications=PUSH_NOTIFICATIONS,
        title=f"{settings.agent_name}-agent",
    )
except CardError as exc:
    # 카드가 반쯤 빈 채로 뜨는 것보다 기동 시점에 죽는 편이 낫다. 오케스트레이터가
    # 발견은 하는데 못 쓰는 에이전트가 제일 나쁘다.
    raise SystemExit(f"[{settings.agent_name}] agent.yaml 을 읽지 못했습니다: {exc}") from exc

log.info("카드 %s | 스킬 %d개 | MCP %s",
         public_base(), len(app.state.card.skills), list(settings.servers()))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
