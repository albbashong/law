"""A2A 태스크 ↔ 모델 루프.

**상태를 바꾸는 코드는 이 파일과 `store.transition` 뿐이다.** 전이가 흩어지면 규격
준수를 테스트로 지킬 수 없다.

재개(SPEC §2.7)가 이 파일의 어려운 부분이다. 되묻고 나서 답을 받으면, 그 답은
**직전에 무엇을 물었고 어떤 후보를 줬는지**를 알아야만 해석된다. 그 상태를 안 들고
있으면 답을 새 질의로 읽고 같은 질문을 반복한다 — 실측에서 통과율 0% 를 만든 실패다.
"""

from __future__ import annotations

import inspect
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from a2a.extensions import PROGRESS, USAGE
from a2a.store import TaskStore
from a2a.types import Artifact, TaskState
from config import settings

#: 되묻기 상태를 나르는 도구 결과의 키. 도구가 이 키로 밝히면 되묻기로 본다.
CLARIFY_STATUS = "clarify"


@dataclass
class ResumeState:
    """왕복을 가로질러 살아남아야 하는 것(SPEC §2.7).

    앞의 셋이 MUST 다 — 없으면 재개가 재개가 아니라 새 요청이 된다. 뒤의 둘은
    SHOULD 이고, 없으면 맥락을 잃고 같은 조회를 다시 한다.

    프로토콜에 안 나오는 에이전트 내부 상태다. 태스크와 수명을 같이한다 —
    `store` 가 태스크를 버릴 때 같이 버려진다.
    """

    original: str = ""                                  # 원래 요청 — MUST
    asked: str = ""                                     # 직전에 한 질문 — MUST
    candidates: list[Any] = field(default_factory=list)  # 제시한 후보 — MUST
    tool_results: dict[str, Any] = field(default_factory=dict)   # SHOULD
    messages: list[dict[str, Any]] = field(default_factory=list)  # SHOULD
    ask_count: int = 0

    def prior(self) -> dict[str, Any]:
        return {"messages": self.messages, "tool_results": self.tool_results}


def resume_query(state: ResumeState, answer: str) -> str:
    """재개 발화를 **후보에 대해** 해석되도록 감싼다.

    답변만 넘기면 모델이 그것을 새 질의로 읽는다 — `"파란 거"` 는 그 자체로 뜻이
    없다. 무엇을 물었고 무엇을 줬는지를 함께 보여야 해석이 성립한다.
    """
    lines = [f"원래 요청: {state.original}"]
    if state.asked:
        lines.append(f"당신이 물었습니다: {state.asked}")
    if state.candidates:
        lines.append("제시한 후보:")
        lines.append(json.dumps(state.candidates, ensure_ascii=False))
    lines.append(f"사용자 답변: {answer}")
    if state.candidates:
        lines.append("이 답변은 위 후보 중 하나를 가리킵니다. "
                     "새 질의로 읽지 말고 후보를 확정해 원래 요청을 마저 처리하세요. "
                     "확정하지 못하면 같은 질문을 반복하지 말고 무엇이 부족한지 밝히세요.")
    else:
        # 고를 것을 주지 않았으면 답이 물음에 대한 답인지 새 질문인지 알 수 없다 — 둘 다 열어 둔다
        lines.append("이 답변이 당신의 물음에 대한 답이면 그것으로 원래 요청을 마저 처리하세요. "
                     "물음과 상관없는 새 질문이면 원래 요청 대신 새 질문으로 처리하세요. "
                     "어느 쪽이든 같은 질문을 반복하지 말고 무엇이 부족한지 밝히세요.")
    return "\n".join(lines)


def _accepts_context(run_query: Any) -> bool:
    """주입된 `run_query` 가 `context` 를 받는가. 서명을 못 읽으면 안 넘긴다(보수적으로)."""
    try:
        params = inspect.signature(run_query).parameters
    except (TypeError, ValueError):
        return False
    return "context" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                      for p in params.values())


class Executor:
    """태스크 하나를 진행시킨다.

    `run_query` 와 `shape_result` 를 주입받는 이유: 서버 계약을 모델·MCP 없이
    테스트하기 위해서다. 매 테스트가 모델을 부르면 느리고 비싸서 아무도 안 돌린다.
    """

    def __init__(self, store: TaskStore, *, run_query=None, shape_result=None) -> None:
        self._store = store
        self._states: dict[str, ResumeState] = {}
        if run_query is None or shape_result is None:      # 실제 기동 경로
            from loop import run_query as _rq
            from shape import shape_result as _sr
            run_query, shape_result = run_query or _rq, shape_result or _sr
        self._run_query = run_query
        self._shape = shape_result
        self._takes_context = _accepts_context(run_query)
        # 호출자가 작업 기억을 돌려주지 못할 때의 대비책 — contextId → 기억 (오래된 것부터 버린다)
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def state_of(self, task_id: str) -> ResumeState:
        return self._states.setdefault(task_id, ResumeState())

    def forget(self, task_id: str) -> None:
        """태스크가 종결되면 내부 상태를 버린다. 안 하면 샌다."""
        self._states.pop(task_id, None)

    async def run(self, task_id: str, text: str, *, resuming: bool,
                  context: dict[str, Any] | None = None) -> None:
        """한 턴을 처리한다. 취소되면 `store.cancel` 이 이 코루틴을 죽인다.

        `context` 는 호출자가 data 파트로 실어 보낸 것이다(코어의 초점 등, SPEC §2.4).
        **주입된 `run_query` 가 받을 줄 알 때만** 넘긴다 — 계약을 넓히면서 구형 주입을
        깨지 않는 방법이다.
        """
        store, state = self._store, self.state_of(task_id)
        context_id = store.get(task_id).context_id or ""
        context = self._with_local_memory(context_id, context)

        # 재개 판정은 **후보가 있는지가 아니라 이전 상태가 있는지**로 한다.
        # 에이전트는 후보 없이 물을 수도 있다("주소를 알려주세요") — 그때 후보를
        # 조건으로 걸면 답변이 새 질의로 읽혀 같은 질문이 되돌아온다.
        if resuming and (state.asked or state.candidates or state.original):
            query, prior = resume_query(state, text), state.prior()
        else:
            state.original = text
            query, prior = text, None

        def on_progress(fraction: float, message: str) -> None:
            store.put_metadata(task_id, PROGRESS,
                               {"fraction": max(0.0, min(fraction, 1.0)),
                                "message": message})

        extra = {"context": context} if context and self._takes_context else {}
        try:
            raw = await self._run_query(query, on_progress=on_progress, prior=prior, **extra)
        except Exception as exc:  # noqa: BLE001 — 실패도 태스크의 정상 종결이다
            store.transition(task_id, TaskState.FAILED,
                             message=f"처리하지 못했습니다: {exc}")
            self.forget(task_id)
            return

        ask = raw.get("ask")
        if isinstance(ask, dict) and ask.get("question"):
            # 후보 없는(또는 모델이 만든 후보의) 되묻기. 성형을 거치지 않는다 — 도구 결과가
            # 아니라 모델의 질문이다. 재개 상태는 도구 되묻기와 같은 자리에 둔다(SPEC §2.7).
            state.tool_results = dict(raw.get("tool_results") or {})
            state.messages = list(raw.get("messages") or [])
            state.asked = str(ask["question"])
            state.candidates = list(ask.get("candidates") or [])
            state.ask_count += 1
            # answer 는 언제나 MUST(규격 4.2절) — 아티팩트를 안 읽는 클라이언트도
            # 물음을 봐야 한다.
            store.put_artifact(task_id, Artifact.text(task_id, "answer", state.asked))
            existing = {a.name for a in (store.get(task_id).artifacts or [])}
            if state.candidates or "candidates" in existing:
                store.put_artifact(task_id, Artifact.data(task_id, "candidates", state.candidates))
            self._publish_usage(task_id, raw.get("usage"))
            store.transition(task_id, TaskState.INPUT_REQUIRED, message=state.asked)
            return

        # 다음 턴이 이어받을 것을 먼저 갈무리한다 — 성형이 실패해도 맥락은 남는다.
        state.tool_results = dict(raw.get("tool_results") or {})
        state.messages = list(raw.get("messages") or [])
        errors = [e for e in (raw.get("errors") or []) if isinstance(e, dict)]

        shaped = self._shape(raw.get("answer") or "", state.tool_results, raw.get("usage"))
        self._publish(task_id, shaped)
        # 이전 턴이 errors 를 실었으면, 이번 턴이 비었어도 [] 로 덮어 낡은 것을 지운다.
        existing_errors = any(a.name == "errors" for a in (store.get(task_id).artifacts or []))
        if errors or existing_errors:
            store.put_artifact(task_id, Artifact.data(task_id, "errors", errors))
        # 답에서 뽑아 원문과 대조한 법령상의 값 — 이전 턴 것이 남지 않게 비었어도 덮는다
        params = [p for p in (raw.get("parameters") or []) if isinstance(p, dict)]
        if params or any(a.name == "parameters" for a in (store.get(task_id).artifacts or [])):
            store.put_artifact(task_id, Artifact.data(task_id, "parameters", params))

        answer = shaped.get("message") or ""

        # 내 일이 아니다(SPEC 5장) — 실패도 완료도 아닌 거절. 클라이언트가 다른 길을 찾게 한다.
        if raw.get("stopped") == "rejected":
            store.transition(task_id, TaskState.REJECTED,
                             message=answer or "이 에이전트가 처리할 요청이 아닙니다.")
            self.forget(task_id)
            return

        # 불통 판정(stopped=="unavailable")이 되묻기보다 먼저다(M1) — 재개로 들어온
        # 애매한 tool_results 가 성형에서 다시 clarify 로 보여도, 실제로는 도구에
        # 못 닿아 확정할 수 없는 상태다. 되묻기로 보내면 같은 질문을 반복하게 된다.
        if raw.get("stopped") == "unavailable":
            # 의존 서비스에 못 닿았다. 재시도 없이 사람이 읽을 사유로 끝낸다(규격 4.3절).
            last = errors[-1] if errors else {"tool": "?", "message": ""}
            store.transition(task_id, TaskState.FAILED,
                             message=settings.fail_unavailable_text.format(
                                 tool=last.get("tool", "?"), message=last.get("message", "")))
            self.forget(task_id)
            return

        if shaped.get("status") == CLARIFY_STATUS:
            asked = shaped.get("message") or "추가 정보가 필요합니다."
            state.asked = asked
            state.candidates = list(shaped.get("candidates") or [])
            state.ask_count += 1
            store.transition(task_id, TaskState.INPUT_REQUIRED, message=asked)
            return

        self._remember(task_id, context_id, state.original, answer, raw, context)

        if not answer and raw.get("stopped") == "rounds":
            store.transition(task_id, TaskState.FAILED,
                             message="라운드 상한 안에 답을 만들지 못했습니다.")
            self.forget(task_id)
            return

        store.transition(task_id, TaskState.COMPLETED,
                         message=answer or "결과가 없습니다.")
        self.forget(task_id)

    def _with_local_memory(self, context_id: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
        """호출자가 memory 를 보내지 않았으면 대비책에 든 같은 대화의 기억을 얹는다."""
        key = settings.memory_key
        if not settings.memory_local or not context_id or (context or {}).get(key):
            return context
        held = self._memory.get(context_id)
        return {**(context or {}), key: held} if held else context

    def _remember(self, task_id: str, context_id: str, question: str, answer: str,
                  raw: dict[str, Any], context: dict[str, Any] | None) -> None:
        """이번 턴을 작업 기억에 더해 아티팩트로 낸다(호출자가 보관해 다음 호출에 돌려준다)."""
        n = settings.memory_turns
        if n <= 0:
            return
        before = (context or {}).get(settings.memory_key)
        turns = before.get("turns") if isinstance(before, dict) else None
        turns = [t for t in turns if isinstance(t, dict)] if isinstance(turns, list) else []
        calls = [c for c in (raw.get("calls") or []) if isinstance(c, dict)][:settings.memory_calls_max]
        turns = [*turns, {"q": question, "a": answer[:settings.memory_answer_chars], "calls": calls}][-n:]
        memory = {"turns": turns}
        self._store.put_artifact(task_id, Artifact.data(task_id, settings.memory_key, memory))
        if settings.memory_local and context_id:
            self._memory[context_id] = memory
            self._memory.move_to_end(context_id)
            while len(self._memory) > settings.memory_local_max:
                self._memory.popitem(last=False)

    def _publish(self, task_id: str, shaped: dict[str, Any]) -> None:
        """성형 결과를 아티팩트와 metadata 로 올린다(SPEC §2.6).

        재개 사이에 값이 비면(F1) 아티팩트를 그대로 두지 않고 빈 값으로 덮어쓴다 —
        안 그러면 확정된 뒤에도 지난 턴의 candidates/actions/resolve 가 그대로 남아
        폴링 클라이언트가 낡은 후보를 다시 본다.
        """
        store = self._store
        existing = {a.name for a in (store.get(task_id).artifacts or [])}
        if message := shaped.get("message"):
            store.put_artifact(task_id, Artifact.text(task_id, "answer", message))
        empty_of: dict[str, Any] = {"actions": [], "candidates": [], "resolve": {}, "facts": {}}
        for name in ("actions", "candidates", "resolve", "facts"):
            value = shaped.get(name)
            if value or name in existing:
                store.put_artifact(task_id, Artifact.data(task_id, name, value or empty_of[name]))
        self._publish_usage(task_id, shaped.get("usage"))

    def _publish_usage(self, task_id: str, usage: Any) -> None:
        if not usage:
            return
        self._store.put_metadata(task_id, USAGE, {
            "tokensIn": int(usage.get("tokens_in") or 0),
            "tokensOut": int(usage.get("tokens_out") or 0),
        })
