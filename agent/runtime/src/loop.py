"""에이전트 자체 모델 루프 — 코어 밖, 이 서버 안에서 돈다.

`run_query` 가 뼈대다: `prompt.txt` 를 시스템 메시지로 깔고, MCP 도구 목록을 모델에
쥐어준 뒤, 모델이 도구를 부르면 실행하고 결과를 되먹이고, 최종 답을 내면 멈춘다.
`mcp`/`model` 을 주입할 수 있어 테스트가 실물 없이 이 로직만 검증한다 — 기본값은
아래의 실물 어댑터(McpAdapter, ModelAdapter)다.

측정된 실패 두 가지를 정면으로 겨냥한다 (2026-08-25 골든셋, 20문항 중 16 실패):
- 10/16: 모델이 도구를 하나도 안 부르고 같은 되묻기를 반복 — `prompt.txt` 의 "같은
  질문을 두 번 하지 마세요" 규칙과, 라운드 상한(`rounds_max`)이 최후 방어선이다.
- 1건: 사용자 발화에 19자리 PNU 가 이미 있는데도 어느 PNU 를 쓸지 "결정 못 하겠다"고
  포기 — `prompt.txt` 의 PNU 규칙이 이를 겨냥한다.

모델은 추론 모델이다: 도구를 부르는 턴에서는 `message.content` 가 `None` 이고 사고
과정은 표준이 아닌 `message.reasoning` 에 담긴다(실측, vLLM 엔드포인트 기준). 루프는
절대 `content` 의 참/거짓으로 갈라서는 안 되고 `finish_reason` 으로만 가른다 —
"tool_calls" 면 도구 실행, "stop" 이면 `content` 가 최종 답이다. `reasoning` 은
내부 사고이지 사용자에게 보일 텍스트가 아니므로 답변에 절대 섞지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from conditions import reversed_conditions
from config import AGENT_DIR, PROMPT_PATH, settings
import rates

logger = logging.getLogger(__name__)



_UNKNOWN_TOOL_TEMPLATE = "모르는 도구입니다: {name}. 목록에 있는 도구만 부르세요."

#: 예약 도구 — MCP 가 아니라 런타임이 처리한다. 모델이 부르면 루프가 멈추고 태스크는
#: INPUT_REQUIRED 로 간다(SPEC §2.6). 도구로 얻을 수 있는 값을 묻는 데 쓰지 않도록
#: 설명이 경계를 밝힌다 — 그 판단 자체는 prompt.txt 의 되묻기 정책이 한다.
ASK_USER = "ask_user"
ASK_USER_TOOL: dict[str, Any] = {
    "name": ASK_USER,
    "description": (
        "필수 정보가 발화에 없어 진행할 수 없을 때만 부른다. 도구로 얻을 수 있는 값"
        "(좌표·PNU)은 묻지 않는다. question 에 사용자에게 보일 질문을 그대로 쓴다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "사용자에게 보일 질문 한 문장"},
            "candidates": {
                "type": "array",
                "description": "고를 것이 있을 때만. 항목은 label(표시)과 id(식별자)",
                "items": {"type": "object"},
            },
        },
        "required": ["question"],
    },
}
#: 예약 도구 — 부르면 마지막 답을 STORY_PROMPT 지시로 한 번 더 불러 이야기로 바꾼다(설정했을 때만 준다).
STORY = "retell_as_story"
STORY_TOOL: dict[str, Any] = {
    "name": STORY,
    "description": (
        "사용자가 예시나 쉬운 설명을 원할 때 먼저 한 번 부른다. 그다음 근거를 조회해 평소 형식으로 사실을 "
        "정확히 답하면, 그 답을 이야기로 바꾸는 일은 런타임이 따로 한다."
    ),
    "input_schema": {"type": "object", "properties": {}},
}
RESERVED_TOOLS = frozenset({ASK_USER, STORY})


def story_forms() -> dict[str, tuple[str, str]]:
    """설명틀 이름 → (고르는 기준 = 첫 줄, 쓰는 법 = 나머지). STORY_FORMS_DIR 이 없으면 빈 dict."""
    if not settings.story_forms_dir:
        return {}
    out = {}
    for f in sorted((AGENT_DIR / settings.story_forms_dir).glob("*.txt")):
        head, _, body = f.read_text(encoding="utf-8").partition("\n")
        out[f.stem] = (head.strip(), body.strip())
    return out


def domains() -> dict[str, tuple[list[str], list[str], str]]:
    """분야 이름 → (켜는 법령명들, 켜는 도구들, 지침). DOMAINS_DIR 이 없으면 빈 dict."""
    if not settings.domains_dir:
        return {}
    out = {}
    for f in sorted((AGENT_DIR / settings.domains_dir).glob("*.txt")):
        head, _, body = f.read_text(encoding="utf-8").partition("\n---\n")
        heads = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
        names = lambda k: [x.strip() for x in heads.get(k, "").split(",") if x.strip()]   # noqa: E731
        out[f.stem] = (names("법령"), names("도구"), body.strip())
    return out


def active_domains(calls: list[dict[str, Any]]) -> list[str]:
    """이번까지의 호출(모델이 연 법령·부른 도구)로 켜지는 분야 — 시행령·시행규칙은 법률 이름으로 시작한다."""
    keys = [k.strip() for k in settings.domain_law_keys.split(",") if k.strip()]
    tools = {c.get("tool") for c in calls}
    laws = [re.sub(r"\s+", "", v) for c in calls for k in keys
            if isinstance(v := (c.get("args") or {}).get(k), str)]
    return [name for name, (dlaws, dtools, _) in domains().items()
            if tools & set(dtools) or any(x.startswith(re.sub(r"\s+", "", d)) for d in dlaws for x in laws)]


def review_wanted(follow_up: bool, calls: list[dict[str, Any]]) -> bool:
    """답 점검 대상인가 — follow_up_or_general 이면 이어지는 질문이거나 분야 도구 없이 법령을 직접 연 답만."""
    if settings.answer_review_scope == "all" or follow_up:
        return True
    tools = {t for _, dtools, _ in domains().values() for t in dtools}
    return not any(c.get("tool") in tools for c in calls)


def with_domains(system_prompt: str, names: list[str]) -> str:
    table = domains()
    return system_prompt + "".join(settings.domain_heading.format(name=n, body=table[n][2]) for n in names)


def story_tool() -> dict[str, Any]:
    forms = story_forms()
    if not forms:
        return STORY_TOOL
    form = {"type": "string", "enum": list(forms),
            "description": "답할 조문의 생김새에 맞는 설명틀 — " + "; ".join(f"{k}: {v[0]}" for k, v in forms.items())}
    return {**STORY_TOOL, "input_schema": {"type": "object", "properties": {"form": form}}}


def story_laws(tool_results: dict[str, Any]) -> list[str]:
    """도구 결과에서 STORY_LAW_KEYS 키의 문자열 값(법령명)을 나온 순서대로, 겹치지 않게."""
    keys = {k.strip() for k in settings.story_law_keys.split(",") if k.strip()}
    found: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in keys and isinstance(x, str) and x.strip() and x not in found:
                    found.append(x)
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    if keys:
        walk(list(tool_results.values()))
    return found[: settings.story_laws_max]
_EMPTY_QUESTION_TEXT = "question 이 비었습니다. 사용자에게 보일 질문을 적어 다시 부르세요."
_ASKED_TEXT = "사용자에게 물었습니다: {question}"
_NOT_EXECUTED_TEXT = "실행하지 않았습니다 — 먼저 사용자에게 물었습니다."
REPEAT_TEXT = (
    "같은 호출을 반복하고 있습니다({name}, {n}회). 같은 인자로는 같은 결과입니다. "
    "앞 결과의 제안·안내대로 인자를 바꾸거나 다른 도구로 찾고, 정보가 부족하면 ask_user 로 물으세요."
)
#: 연결 불통으로 볼 httpx/httpcore 예외 이름. 모듈까지 함께 본다(이름만 보면 다른 라이브러리의
#: 같은 이름과 섞인다).
_TRANSPORT_ERROR_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "NetworkError", "RemoteProtocolError", "ReadError", "WriteError",
})


class McpUnavailable(RuntimeError):
    """MCP 서버에 아예 닿지 못했다. 재시도는 낭비다(규격 4.3절) — 즉시 보고한다."""

    def __init__(self, server: str, cause: BaseException) -> None:
        super().__init__(f"{server}: {cause}")
        self.server = server
        self.cause = cause


def is_unavailable(exc: BaseException) -> bool:
    """'도구가 안 된다고 했다'(재시도 여지 있음)와 '도구가 거기 없다'(없음)를 가른다."""
    if isinstance(exc, McpUnavailable):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(is_unavailable(e) for e in exc.exceptions)
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    module = type(exc).__module__ or ""
    return module.startswith(("httpx", "httpcore")) and type(exc).__name__ in _TRANSPORT_ERROR_NAMES


def args_hash(args: Mapping[str, Any]) -> str:
    """(도구, 인자) 반복을 세는 키. 키 순서와 무관하다."""
    body = json.dumps(dict(args), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def result_marks(text: str) -> set[str]:
    """결과의 근거 표지(인용 패턴에 맞는 조문 번호들) — 표현만 바꾼 호출이 새 근거를 냈는지 본다.
    패턴이 없으면 결과 전체가 하나의 표지다."""
    if not settings.answer_cite_pattern:
        return {hashlib.sha1(text.encode()).hexdigest()}
    return {re.sub(r"\s+", "", m.group(0)) for m in re.finditer(settings.answer_cite_pattern, text)}


def _failure(tool: str, exc: BaseException, attempts: int) -> dict[str, Any]:
    unavailable = is_unavailable(exc)
    message = str(getattr(exc, "cause", exc) if isinstance(exc, McpUnavailable) else exc)
    return {"tool": tool, "kind": "unavailable" if unavailable else "tool_error",
            "message": message[:500], "recoverable": not unavailable, "attempts": attempts}


def ask_from(args: Mapping[str, Any]) -> dict[str, Any]:
    """`ask_user` 인자 → {"question", "candidates"}. label 없는 후보는 버리고
    id 없는 후보는 번호를 준다."""
    question = str(args.get("question") or "").strip()
    raw = args.get("candidates")
    candidates: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, Mapping) and item.get("label"):
                shaped = dict(item)
                shaped.setdefault("id", str(index))
                candidates.append(shaped)
    return {"question": question, "candidates": candidates}


class Mcp(Protocol):
    """루프가 기대하는 MCP 클라이언트 모양 — 테스트의 FakeMcp 도 이 모양을 따른다."""

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call(self, name: str, args: dict[str, Any]) -> Any: ...


class Model(Protocol):
    """루프가 기대하는 모델 어댑터 모양 — 테스트의 FakeModel 도 이 모양을 따른다."""

    async def next_step(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], require: bool = False
    ) -> dict[str, Any]: ...


def _tool_calls_from_step(step: dict[str, Any]) -> list[dict[str, Any]]:
    """모델의 한 턴을 도구 호출 목록으로 통일한다.

    실물 모델(ModelAdapter)은 한 턴에 도구를 여럿 부를 수 있어 "tools" 리스트로
    낸다. 테스트의 FakeModel 은 한 번에 하나만 내는 "tool" 단수 표기를 쓴다 — 둘 다
    받아들인다.
    """
    if "tools" in step:
        return list(step["tools"])
    return [{"id": step.get("id"), "name": step["tool"], "args": step.get("args", {})}]


def _assistant_message(step: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """모델에게 되먹일 assistant 메시지. 실물 응답이 있으면 그대로 쓴다."""
    raw = step.get("raw_assistant_message")
    if raw is not None:
        return raw
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": c.get("id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c.get("args", {}), ensure_ascii=False),
                },
            }
            for i, c in enumerate(calls)
        ],
    }


#: 코어(호출자)가 이번 호출에서 원하는 것 — data 파트의 이 키만 읽는다.
FOCUS_KEY = "focus"
FOCUS_LINE = "[오케스트레이터 초점] {focus}"


#: 코어가 함께 주는 "이번 턴까지 확인된 사실"(data 파트). 앞 턴에서 확인한 대상이 여기 남아 있다.
FACTS_KEY = "facts"
FACTS_LINE = "[확인된 사실] {facts}"
#: 사실 줄의 길이 상한 — 프롬프트를 잡아먹지 않게 한다.
FACTS_MAX_CHARS = 600


def ungrounded(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답변의 인용(ANSWER_CITE_PATTERN) 중 도구 결과·사용자 발화 어디에도 없는 것 (공백 무시, 번호 경계).

    `skip` 은 검증이 스스로 넣은 되돌림 문장 — 걸린 인용을 담고 있으므로 근거로 치지 않는다.
    """
    pattern = settings.answer_cite_pattern
    if not pattern or not answer:
        return []
    corpus = "".join(re.sub(r"\s+", "", str(m.get("content") or ""))
                     for m in messages if m.get("role") in ("tool", "user") and m.get("content") not in skip)
    out: list[str] = []
    for hit in re.findall(pattern, answer):
        cite = hit if isinstance(hit, str) else next((h for h in hit if h), "")
        key = re.sub(r"\s+", "", cite)
        if key and not re.search(re.escape(key) + r"(?!\d|의\d)", corpus) and cite not in out:
            out.append(cite)
    return out


def _known_pairs(messages: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, tuple[str, list[str]]]:
    """도구 결과(JSON)에서 (출처명, 인용) 짝을 모은다 → {공백 뺀 출처명: (원래 이름, [공백 뺀 인용])}."""
    src, cite = keys
    out: dict[str, tuple[str, list[str]]] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if isinstance(v.get(src), str) and isinstance(v.get(cite), str):
                name = v[src]
                out.setdefault(re.sub(r"\s+", "", name), (name, []))[1].append(re.sub(r"\s+", "", v[cite]))
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for m in messages:
        if m.get("role") == "tool":
            try:
                walk(json.loads(m.get("content") or ""))
            except ValueError:
                continue    # 잘린 결과는 짝을 읽지 않는다
    return out


TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def misattributed(story: str, answer: str) -> list[str]:
    """이야기 문장이 붙인 조문이 사실 답의 근거표에서 다른 내용의 행인 경우.

    답의 표(법령 | 조문 | 내용 …)에서 행마다 그 행에만 있는 말(다른 행의 내용에 없는 말)을 모은다. 이야기의 한 문장(표의
    한 행도 한 문장으로 본다)이 어떤 행의 조문을 적었는데 그 행에만 있는 말을 하나도 담지 않았으면 다른 내용에 그 조문을
    붙인 것으로 본다. 그 행에만 있는 말이 없는 행은 보지 않는다. 낱말 목록 없이 답의 표에서만 판단한다.
    """
    rows = []
    for line in answer.splitlines():
        m = TABLE_ROW.match(line)
        cells = [c.strip() for c in m.group(1).split("|")] if m else []
        if len(cells) >= 3 and not set(cells[1]) <= set("-: ") and re.search(r"\d", cells[1]):
            rows.append((cells[0], cells[1], " ".join(cells[2:])))
    if len(rows) < 2:
        return []
    words = [set(WORD.findall(r[2])) - set(WORD.findall(r[0] + " " + r[1])) for r in rows]
    only = [w - set().union(*(words[:i] + words[i + 1:])) for i, w in enumerate(words)]
    squash = lambda t: re.sub(r"\s+", "", t)  # noqa: E731
    problems = []
    for sentence in (x.strip() for x in re.split(r"(?<=[.!?。])\s+|\n", story) if x.strip()):
        flat = squash(sentence)
        for (law, article, content), mine in zip(rows, only):
            same = [r for r in rows if squash(r[1]) == squash(article)]
            if (not mine or squash(article) not in flat
                    or (len(same) > 1 and squash(law) not in flat) or any(w in sentence for w in mine)):
                continue
            # 같은 조문 문자열이 더 긴 다른 조문의 일부일 뿐인 경우(제2호 ⊂ 제2호가목)는 그 긴 조문 행이 맡는다
            if any(squash(article) != squash(r[1]) and squash(article) in squash(r[1]) and squash(r[1]) in flat
                   for r in rows):
                continue
            problems.append(settings.story_misattributed_text.format(
                sentence=sentence[:80], article=article, row=content[:60]))
    return problems


def beyond_answer(story: str, answer: str) -> list[str]:
    """이야기가 붙인 조문(항·호·목까지) 가운데 사실 답에 없는 것 — 이야기는 답을 다시 말하는 것이라 답의 조문만 쓴다.
    인용 검사(ungrounded)는 조·별표 번호까지만 보고 조회 원문 전체와 대조해, 답에 없는 다른 목을 붙여도 통과한다."""
    if not settings.answer_cite_pattern:
        return []
    flat = re.sub(r"\s+", "", answer)
    out: list[str] = []
    for m in re.finditer(f"(?:{settings.answer_cite_pattern}){settings.story_cite_tail_pattern}", story):
        cite = m.group(0).strip()
        if re.sub(r"\s+", "", cite) not in flat and cite not in out:
            out.append(cite)
    return [settings.story_beyond_answer_text.format(cite=c) for c in out]


def mispaired(answer: str, messages: list[dict[str, Any]]) -> list[str]:
    """앞말이 도구 결과의 출처명으로 끝나는데 그 출처에 없는 인용 ("건축법 시행령 제84조" 처럼 짝이 틀린 것).

    앞말이 어느 출처명으로도 끝나지 않으면(약칭·문장) 판단하지 않는다.
    """
    pattern, keys = settings.answer_pair_pattern, settings.answer_pair_key_names()
    if not pattern or len(keys) != 2 or not answer:
        return []
    known = _known_pairs(messages, keys)
    out: list[str] = []
    for m in re.finditer(pattern, answer):
        scope = re.sub(r"\s+", "", m.group("scope") or "")
        cite = re.sub(r"\s+", "", m.group("cite") or "")
        names = [k for k in known if scope.endswith(k)]
        if not names or not cite:
            continue
        name, cites = known[max(names, key=len)]
        if not any(re.match(re.escape(cite) + r"(?!\d|의\d)", c) for c in cites):
            label = f"{name} {m.group('cite').strip()}"
            if label not in out:
                out.append(label)
    return out


def _cited_scopes(answer: str, messages: list[dict[str, Any]]) -> list[str]:
    """도구 결과 중 답이 인용한 (출처명, 인용) 짝의 부분만 — 짝을 직접 가진 dict(검색 한 줄)나, 짝을 가진 dict 를
    바로 아래 둔 dict(조문 결과와 그 호출인자)를 통째로 꺼낸다."""
    pattern, keys = settings.answer_pair_pattern, settings.answer_pair_key_names()
    if not pattern or len(keys) != 2 or not answer:
        return []
    src, cite = keys
    flat = re.sub(r"\s+", "", answer)
    cited = {re.sub(r"\s+", "", m.group("cite") or "") for m in re.finditer(pattern, answer)} - {""}

    def pair(v: Any) -> tuple[str, str] | None:
        if isinstance(v, dict) and isinstance(v.get(src), str) and isinstance(v.get(cite), str):
            return re.sub(r"\s+", "", v[src]), re.sub(r"\s+", "", v[cite])
        return None

    def hit(p: tuple[str, str] | None) -> bool:
        return bool(p) and p[0] in flat and any(re.match(re.escape(c) + r"(?!\d|의\d)", p[1]) for c in cited)

    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if hit(pair(v)) or any(hit(pair(x)) for x in v.values()):
                out.append(json.dumps(v, ensure_ascii=False))
                return
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for m in messages:
        if m.get("role") == "tool":
            try:
                walk(json.loads(m.get("content") or ""))
            except ValueError:
                continue
    return out


def rate_unmatched(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답의 비율 중 원문 값과 맞지 않는 것 — 답이 인용한 조문의 결과가 있으면 그것(과 사용자 발화)하고만 대조한다.
    다른 조문에 우연히 같은 값이 있어 통과하는 것을 막는다. 인용과 맞는 결과가 없으면 이번 조회 전체와 대조한다."""
    scopes = _cited_scopes(answer, messages)
    if scopes:
        messages = [m for m in messages if m.get("role") == "user"] + [{"role": "tool", "content": t} for t in scopes]
    return rates.unmatched(answer, messages, skip)


def unnamed_laws(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답에 쓴 법령명(ANSWER_LAW_PATTERN) 중 도구 결과·사용자 발화에 없는 것 (공백 무시)."""
    pattern = settings.answer_law_pattern
    if not pattern or not answer:
        return []
    corpus = "".join(re.sub(r"\s+", "", str(m.get("content") or ""))
                     for m in messages if m.get("role") in ("tool", "user") and m.get("content") not in skip)
    out: list[str] = []
    for hit in re.findall(pattern, answer):
        name = (hit if isinstance(hit, str) else next((h for h in hit if h), "")).strip()
        if name and re.sub(r"\s+", "", name) not in corpus and name not in out:
            out.append(name)
    return out


def _law_names(messages: list[dict[str, Any]], skip: tuple[str, ...]) -> list[str]:
    """도구 결과의 ANSWER_LAW_KEYS 값과 사용자 발화에서 ANSWER_LAW_LOOSE_PATTERN 으로 잡은 이름 — 공백 뺀 꼴."""
    keys = {k.strip() for k in settings.answer_law_keys.split(",") if k.strip()}
    names: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in keys:
                    names.extend(n for n in ([x] if isinstance(x, str) else x if isinstance(x, list) else [])
                                 if isinstance(n, str))
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for m in messages:
        content = m.get("content")
        if not isinstance(content, str) or content in skip:
            continue
        if m.get("role") == "tool":
            try:
                walk(json.loads(content))
            except ValueError:
                pass
        elif m.get("role") == "user":
            names += re.findall(settings.answer_law_loose_pattern, content)
    return [b for b in (re.sub(r"\s+", "", n) for n in names) if b]


def loose_unnamed_laws(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답에 「」 없이 쓴 법령 정식명(ANSWER_LAW_LOOSE_PATTERN) 중 이번 대화에 나온 법령명으로 끝나지 않는 것.

    잡힌 말에 앞말이 붙을 수 있어 끝이 맞는지를 보고, 앞말 없이 이름 일부만 쓴 경우를 위해 반대 방향도 본다(공백 무시).
    """
    pattern = settings.answer_law_loose_pattern
    if not pattern or not answer:
        return []
    known = _law_names(messages, skip)
    out: list[str] = []
    for hit in re.findall(pattern, answer):
        name = (hit if isinstance(hit, str) else next((h for h in hit if h), "")).strip()
        bare = re.sub(r"\s+", "", name)
        if name and not any(bare.endswith(k) or k.endswith(bare) for k in known) and name not in out:
            out.append(name)
    return out


def _bare(text: str) -> str:
    """글자·숫자만 — 인용 대조용. JSON 에 실린 줄바꿈 표기(\\n)는 먼저 띄어 쓴다."""
    return re.sub(r"[^\w]|_", "", re.sub(r"\\[nrt]", " ", text))


def misquoted(answer: str, messages: list[dict[str, Any]], skip: tuple[str, ...] = ()) -> list[str]:
    """답의 따옴표 인용문(ANSWER_QUOTE_PATTERN) 중 도구 결과·사용자 발화에 그대로 없는 것.

    조문 번호가 적힌 줄만 보고, 공백·문장부호는 무시하며, 줄임표(… ...)로 줄인 인용은 조각마다 본다. ANSWER_QUOTE_MIN_CHARS 보다 짧은
    조각(낱말 하나를 따옴표로 묶은 것)은 보지 않는다.
    """
    pattern = settings.answer_quote_pattern
    if not pattern or not answer:
        return []
    corpus = "".join(_bare(str(m.get("content") or ""))
                     for m in messages if m.get("role") in ("tool", "user") and m.get("content") not in skip)
    # 조문 번호(ANSWER_CITE_PATTERN)가 함께 적힌 줄의 인용만 원문 인용으로 본다 — 질문을 따옴표로 다시 적은 것은 뺀다
    lines = [ln for ln in answer.splitlines()
             if not settings.answer_cite_pattern or re.search(settings.answer_cite_pattern, ln)]
    out: list[str] = []
    for hit in re.findall(pattern, "\n".join(lines)):
        quote = (hit if isinstance(hit, str) else next((h for h in hit if h), "")).strip()
        parts = [_bare(x) for x in re.split(r"…|\.{3,}", quote)]
        if any(len(x) >= settings.answer_quote_min_chars and x not in corpus for x in parts) and quote not in out:
            out.append(quote)
    return out


def unquote(text: str, quotes: list[str]) -> str:
    """그 인용문들을 감싼 따옴표를 벗긴다(글은 그대로)."""
    for q in quotes:
        for o, c in (("“", "”"), ('"', '"'), ("‘", "’")):
            text = text.replace(f"{o}{q}{c}", q)
    return text


def clean_answer(answer: str) -> str:
    """ANSWER_STRIP_PATTERN 에 맞는 표기(본문에 흘린 도구 호출 등)를 지운다."""
    return re.sub(settings.answer_strip_pattern, "", answer) if settings.answer_strip_pattern else answer


def internal_names(answer: str, names: set[str] | list[str]) -> list[str]:
    """답 본문에 나온 내부 이름(이번 실행의 도구 이름) — 사용자에게 보일 말이 아니다."""
    return sorted(n for n in names if n and re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", answer))


def drop_internal(answer: str, names: list[str]) -> str:
    """내부 이름이 든 문장(표는 그 행)을 뺀다 — 되돌린 뒤에도 남았을 때의 마지막 정리."""
    if not names:
        return answer
    hit = re.compile("|".join(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])" for n in names))
    out = []
    for line in answer.split("\n"):
        if not hit.search(line):
            out.append(line)
        elif not line.lstrip().startswith("|"):
            kept = " ".join(x for x in re.split(r"(?<=[.!?。])\s+", line.strip()) if not hit.search(x))
            if kept:
                out.append(kept)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _flat(messages: list[dict[str, Any]]) -> str:
    """사용자 발화·도구 결과를 공백 없이 이은 것 — 값이 대화에 나왔는지 볼 말뭉치."""
    return "".join(re.sub(r"\s+", "", str(m.get("content") or ""))
                   for m in messages if m.get("role") in ("tool", "user"))


def ungrounded_arg(args: Mapping[str, Any], messages: list[dict[str, Any]]) -> tuple[str, str] | None:
    """ARG_GROUNDED_KEYS 인자 중 대화에 없는 값 → (키, 값). 모두 있으면 None.

    숫자가 든 낱말(번지)은 모두 나와야 하고, 나머지 낱말(지명)은 하나 이상 나와야 한다 — 시도를 덧붙여
    쓰는 건("경산시" → "경상북도 경산시") 정상이다. 지명 끝 한 글자(시·군·구 등)는 떼고도 비교한다.
    """
    keys = settings.arg_grounded_names()
    if not keys:
        return None
    corpus = None
    for key in keys:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        corpus = corpus if corpus is not None else _flat(messages)
        toks = value.split()
        nums = [t for t in toks if re.search(r"\d", t)]
        names = [t for t in toks if t not in nums]
        seen = lambda t: t in corpus or (len(t) >= 3 and t[:-1] in corpus)  # noqa: E731
        if not all(t in corpus for t in nums) or (names and not any(seen(t) for t in names)):
            return key, value
    return None


async def retell(model: Any, question: str, answer: str, messages: list[dict[str, Any]],
                 cite_notes: tuple[str, ...], usage_total: dict[str, int],
                 form: str | None = None, laws: list[str] | None = None) -> str:
    """검증을 마친 답을 STORY_PROMPT 지시로 이야기로 바꾼다 — 도구 없이 한 번만 부른다.

    이번 턴 조회 원문도 함께 넘긴다. STORY_PLAN_PROMPT 가 있고 조회 결과에 대조할 키가 있으면 먼저 계획을
    세워 대조한 뒤 쓴다. 이야기도 형식·인용 검증을 거치고, 걸리면 STORY_ATTEMPTS 까지
    다시 쓰게 한 뒤에도 걸리면 사실 답을 그대로 낸다. 지시문의 {법령}·{설명틀}·{설명틀_쓰는법}은 조회한
    법령명과 설명틀로 채운다 — 틀은 STORY_FORM_SELECT_PROMPT 가 있으면 검증된 답을 보고 다시 고르고,
    못 고르면 모델이 도구로 고른 것, 모르는 틀이면 일반 지시만. STORY_REVIEW_PROMPT 가 있으면 쓴 이야기를
    답과 대조해 고칠 점이 나오면 그것을 붙여 다시 쓰게 한다.
    """
    system = (AGENT_DIR / settings.story_prompt).read_text(encoding="utf-8").replace("{법령}", "·".join(laws or []) or "법령")
    sources = story_sources(messages)
    request = settings.story_request_text.format(question=question, answer=answer, sources=sources)

    async def ask(prompt: str, user: str | None = None) -> str:
        step = await model.next_step([{"role": "system", "content": prompt},
                                      {"role": "user", "content": request if user is None else user}], [])
        for k in ("tokens_in", "tokens_out"):
            usage_total[k] += (step.get("usage") or {}).get(k) or 0
        return (step.get("answer") or "").strip()

    forms = story_forms()
    if forms and settings.story_form_select_prompt:     # 검증된 답을 본 뒤에 틀을 다시 고른다
        menu = "\n".join(f"- {k}: {v[0]}" for k, v in forms.items())
        picked = await ask((AGENT_DIR / settings.story_form_select_prompt).read_text(encoding="utf-8")
                           .replace("{설명틀_목록}", menu))
        hits = sorted((picked.find(k), k) for k in forms if k in picked)
        form = hits[0][1] if hits else form
    chosen = forms.get(form or "")
    system = system.replace("{설명틀}", form if chosen else "일반").replace("{설명틀_쓰는법}", chosen[1] if chosen else "")

    keys = [k.strip() for k in settings.story_plan_grounded_keys.split(",") if k.strip()]
    if settings.story_plan_prompt and (not keys or any(f'"{k}"' in sources for k in keys)):
        plan_prompt = (AGENT_DIR / settings.story_plan_prompt).read_text(encoding="utf-8")
        for _ in range(max(1, settings.story_attempts)):
            plan = checked_plan(await ask(plan_prompt), sources, messages, cite_notes)
            if plan:
                break
        else:
            logger.info("story: form=%s no checked plan — factual answer", form)
            return answer
        request += settings.story_plan_text.format(plan=json.dumps(plan, ensure_ascii=False))
    review = (AGENT_DIR / settings.story_review_prompt).read_text(encoding="utf-8") if settings.story_review_prompt else ""
    feedback = ""
    for attempt in range(1, max(1, settings.story_attempts) + 1):
        story = await ask(system, request + feedback)
        why = ("empty" if not story else "format" if settings.story_format_pattern
               and not re.match(settings.story_format_pattern, story) else "")
        why = why or "; ".join(f"{k}={v}" for k, v in (("cites", ungrounded(story, messages, cite_notes)),
                                                       ("pairs", mispaired(story, messages))) if v)
        if not why and (quotes := misquoted(story, messages, cite_notes)):
            # 원문에 없는 따옴표 인용 — 다시 써도 같은 인용을 지어내곤 한다. 따옴표만 벗겨 원문 인용으로 보이지 않게 한다
            logger.info("story: form=%s attempt=%d unquoted %s", form, attempt, quotes)
            story = unquote(story, quotes)
        if why:
            logger.info("story: form=%s attempt=%d rejected (%s): %s", form, attempt, why, story[:80].replace("\n", " "))
            continue
        # 답의 근거표와 어긋나게 붙인 조문 — 모델 점검 전에 기계로 본다(걸리면 점검을 부르지 않고 바로 다시 쓰게)
        problems = beyond_answer(story, answer) + misattributed(story, answer)
        if not problems and review:
            problems = review_problems(await ask(review, settings.story_review_request_text.format(
                question=question, answer=answer, sources=sources, story=story)))
        if not problems:
            logger.info("story: form=%s laws=%s attempt=%d ok", form, laws, attempt)
            return story
        logger.info("story: form=%s attempt=%d review: %s", form, attempt, problems)
        feedback = settings.story_review_retry_text.format(problems="\n".join(f"- {p}" for p in problems))
    return answer


def review_problems(text: str, key: str | None = None) -> list[str]:
    """점검 답 → 고칠 점 목록. 읽을 수 없으면 빈 목록(점검이 막지 않는다)."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return []
    items = data.get(key or settings.story_review_key) if isinstance(data, dict) else None
    return [str(x) for x in items if str(x).strip()] if isinstance(items, list) else []


async def answer_review(model: "ModelAdapter", question: str, answer: str, messages: list[dict[str, Any]],
                        usage_total: dict[str, int], hints: list[str] | None = None) -> list[str]:
    """ANSWER_REVIEW_PROMPT 로 답을 조회 원문과 대조한 고칠 점. 조회가 없거나 프롬프트가 없으면 빈 목록.
    `hints` 는 기계 대조가 의심한 문장 — 점검 요청 뒤에 붙여 원문과 대조해 판단하게 한다."""
    sources = story_sources(messages)
    if not settings.answer_review_prompt or not sources:
        return []
    prompt = (AGENT_DIR / settings.answer_review_prompt).read_text(encoding="utf-8")
    step = await model.next_step([{"role": "system", "content": prompt}, {"role": "user", "content":
                                  settings.answer_review_request_text.format(question=question, answer=answer, sources=sources)
                                  + (settings.answer_review_hint_text.format(conds="\n".join(f"- {h}" for h in hints))
                                     if hints else "")}], [])
    for k in ("tokens_in", "tokens_out"):
        usage_total[k] += (step.get("usage") or {}).get(k) or 0
    return review_problems((step.get("answer") or "").strip(), settings.answer_review_key)


def checked_plan(text: str, sources: str, messages: list[dict[str, Any]],
                 cite_notes: tuple[str, ...]) -> dict[str, Any] | None:
    """계획 답 → JSON 객체. STORY_PLAN_GROUNDED_KEYS 의 값은 (비워 두지 않았다면) 조회 원문에 그대로(공백 무시)
    있어야 하고, 계획 전체가 인용 검증을 통과해야 한다. 아니면 None."""
    start, end = text.find("{"), text.rfind("}")
    try:
        plan = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    if not isinstance(plan, dict):
        return None
    flat = re.sub(r"\s+", "", sources)
    for key in (k.strip() for k in settings.story_plan_grounded_keys.split(",") if k.strip()):
        value = plan.get(key)
        values = [v for v in (value if isinstance(value, list) else [value]) if v not in (None, "")]
        if any(not isinstance(v, str) or re.sub(r"\s+", "", v) not in flat for v in values):
            return None
    dumped = json.dumps(plan, ensure_ascii=False)
    return None if ungrounded(dumped, messages, cite_notes) or mispaired(dumped, messages) else plan


def story_sources(messages: list[dict[str, Any]]) -> str:
    """이번 턴 도구 결과(예약 도구 제외, 같은 것은 한 번) — 이야기 단계가 원문의 호·목 구조를 직접 읽도록."""
    seen = [m["content"] for m in messages if m.get("role") == "tool" and m.get("name") not in RESERVED_TOOLS
            and isinstance(m.get("content"), str)]
    text = "\n\n".join(dict.fromkeys(seen))
    limit = settings.story_sources_max_chars
    return text if limit <= 0 or len(text) <= limit else text[:limit] + "\n[이하 생략]"


async def extract_parameters(model: Any, question: str, answer: str, messages: list[dict[str, Any]],
                             usage_total: dict[str, int]) -> list[dict[str, Any]]:
    """PARAM_PROMPT 로 답에 나온 법령상의 값을 목록으로 뽑는다 — 도구 없이 한 번 부른다.

    모델이 뽑은 값은 믿지 않고 원문과 대조한다: 값과 단위를 붙인 글자, 근거의 법령명과 조문 번호가 이번 턴 도구
    결과에 공백 없이 그대로 있어야 남긴다. 읽을 수 없으면 빈 목록이다.
    """
    if not settings.param_prompt:
        return []
    prompt = (AGENT_DIR / settings.param_prompt).read_text(encoding="utf-8")
    request = settings.param_request_text.format(question=question, answer=answer, sources=story_sources(messages))
    step = await model.next_step([{"role": "system", "content": prompt}, {"role": "user", "content": request}], [])
    for k in ("tokens_in", "tokens_out"):
        usage_total[k] += (step.get("usage") or {}).get(k) or 0
    reply = (step.get("answer") or "").strip()
    start, end = reply.find("["), reply.rfind("]")
    try:
        items = json.loads(reply[start:end + 1]) if 0 <= start < end else []
    except ValueError:
        logger.info("parameters unreadable: %s", reply[:300])
        return []
    corpus = "".join(_bare(str(m.get("content") or "")) for m in messages if m.get("role") == "tool")
    kept, dropped = [], []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        value = _bare(str(it.get(settings.param_value_key) or ""))
        unit = _bare(str(it.get(settings.param_unit_key) or ""))
        # 값에 단위까지 적었으면(수 아닌 글자가 있으면) 값 그대로, 아니면 값과 단위를 붙인 글자로 대조한다
        value = value if re.search(r"[^\d.,]", value) else value + unit
        basis = str(it.get(settings.param_basis_key) or "")
        m = re.search(r"제\s*\d", basis)
        law, article = (basis[:m.start()], basis[m.start():]) if m else (basis, "")
        ok = value and value in corpus and _bare(law) and _bare(law) in corpus and _bare(article) in corpus
        (kept if ok else dropped).append(it)
    logger.info("parameters: kept=%d dropped=%s", len(kept), dropped or ([] if items else reply[:300]))
    return kept


def last_question(context: dict[str, Any] | None) -> str:
    """작업 기억의 마지막 턴 질문."""
    mem = context.get(settings.memory_key) if isinstance(context, dict) else None
    turns = mem.get("turns") if isinstance(mem, dict) else None
    last = turns[-1] if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else {}
    return str(last.get("q") or "").strip()


def last_calls(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """작업 기억의 마지막 턴에서 한 조회들 → [{"tool", "args"}]. 모양이 맞지 않는 것은 뺀다."""
    mem = context.get(settings.memory_key) if isinstance(context, dict) else None
    turns = mem.get("turns") if isinstance(mem, dict) else None
    last = turns[-1] if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else {}
    return [{"tool": c["tool"], "args": dict(c.get("args") or {})} for c in last.get("calls") or []
            if isinstance(c, dict) and isinstance(c.get("tool"), str) and isinstance(c.get("args") or {}, dict)]


def previous_work(context: dict[str, Any] | None) -> str:
    """호출자가 돌려준 작업 기억(data 파트) → 이전 작업 블록. 모양이 맞지 않으면 빈 문자열."""
    mem = context.get(settings.memory_key) if isinstance(context, dict) else None
    turns = mem.get("turns") if isinstance(mem, dict) else None
    if not isinstance(turns, list):
        return ""
    lines = [settings.memory_heading]
    for i, t in enumerate((t for t in turns if isinstance(t, dict)), start=1):
        lines.append(f"{i}) 질문: {t.get('q', '')}")
        if t.get("a"):
            lines.append(f"   답: {t['a']}")
        for c in t.get("calls") or []:
            if isinstance(c, dict):
                lines.append(f"   조회: {c.get('tool')} {json.dumps(c.get('args') or {}, ensure_ascii=False)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def confirmed_facts(context: dict[str, Any] | None) -> str:
    """코어가 넘긴 사실을 한 줄로. 자리 이름의 뜻은 코어 것이라 해석하지 않고 값만 옮긴다."""
    raw = context.get(FACTS_KEY) if isinstance(context, dict) else None
    if not isinstance(raw, dict) or not raw:
        return ""
    text = ", ".join(f"{k}={v}" for k, v in raw.items())
    return text if len(text) <= FACTS_MAX_CHARS else text[:FACTS_MAX_CHARS - 1] + "…"


def with_focus(query: str, context: dict[str, Any] | None) -> str:
    """사용자 메시지 = 발화 원문 (+ 초점 한 줄) (+ 확인된 사실 한 줄).

    초점이 없거나 발화와 같으면 그 줄을 빼고, 사실이 없으면 그 줄도 뺀다. 사실 줄이 있어야
    "거기"·"그 필지" 처럼 앞 턴을 가리키는 발화에서 대상을 다시 묻지 않는다(멀티턴 실측 2026-09-14).
    """
    focus = str((context or {}).get(FOCUS_KEY) or "").strip() if isinstance(context, dict) else ""
    lines = [query]
    if focus and focus != query.strip():
        lines.append(FOCUS_LINE.format(focus=focus))
    if facts := confirmed_facts(context):
        lines.append(FACTS_LINE.format(facts=facts))
    return "\n".join(lines)


async def restated(model: Any, text: str, usage_total: dict[str, int],
                   tools: list[dict[str, Any]] | None = None) -> tuple[str, list[str], bool, bool, str]:
    """RESTATE_PROMPT 로 발화를 정리한 줄들(중립 질문 + 짐작) — 도구 없이 한 번 부른다.

    같은 뜻을 다른 말투로 물어도 검색어·범위 판단·결론이 흔들리지 않게 하려는 것이다(일상어·약칭으로
    엉뚱한 법령을 찾고, "안 되죠?" 같은 짐작에 결론이 끌려가던 문제 — 실측 2026-10-27). 발화 원문은 고치지
    않는다. 읽을 수 없거나, 정리한 질문에 발화에 없는 숫자나 법령명이 들어가면(지어낸 값·기억으로 푼 별칭)
    아무것도 붙이지 않는다. 함께 돌려주는 확인할 말(RESTATE_TERMS_KEY)은 발화에 실제로 있는 것만 남긴다.
    셋째 값은 예시가 필요한 질문이라는 판단(RESTATE_STORY_KEY), 넷째는 앞 질문에 이어지는 질문이라는 판단
    (RESTATE_FOLLOW_KEY) — 정리한 질문을 버려도 판단은 쓴다. 다섯째는 먼저 부를 도구(RESTATE_TOOL_KEY) — `tools` 의
    이름과 설명 첫 줄을 정리 지시 뒤에 보이고, 고른 이름이 그 목록에 있을 때만 돌려준다.
    """
    if not settings.restate_prompt:
        return "", [], False, False, ""
    prompt = (AGENT_DIR / settings.restate_prompt).read_text(encoding="utf-8")
    allowed = {n.strip() for n in settings.restate_lead_tools.split(",") if n.strip()}
    catalog = [t for t in tools or [] if not allowed or t["name"] in allowed] if settings.restate_tool_key else []
    offered = {t["name"] for t in catalog}
    if catalog:
        prompt += "\n\n" + "\n".join(settings.restate_tool_line.format(
            name=t["name"], description=(t.get("description") or "").strip().split("\n")[0]) for t in catalog)
    step = await model.next_step([{"role": "system", "content": prompt}, {"role": "user", "content": text}], [])
    for k in ("tokens_in", "tokens_out"):
        usage_total[k] += (step.get("usage") or {}).get(k) or 0
    reply = (step.get("answer") or "").strip()
    start, end = reply.find("{"), reply.rfind("}")
    try:
        data = json.loads(reply[start:end + 1]) if 0 <= start < end else {}
    except ValueError:
        return "", [], False, False, ""

    def flag(key: str) -> bool:
        want = data.get(key) if key and isinstance(data, dict) else None
        return want is True or (isinstance(want, str) and want.strip().lower() == "true")
    story, follow = flag(settings.restate_story_key), flag(settings.restate_follow_key)
    lead = data.get(settings.restate_tool_key) if offered and isinstance(data, dict) else None
    lead = lead.strip() if isinstance(lead, str) and lead.strip() in offered else ""
    question = str((data or {}).get(settings.restate_question_key) or "").strip() if isinstance(data, dict) else ""
    presumption = str(data.get(settings.restate_presumption_key) or "").strip() if question else ""
    if not question or any(n not in text for n in re.findall(r"\d+(?:\.\d+)?", question + presumption)):
        return "", [], story, follow, lead
    bare_text = re.sub(r"\s+", "", text)
    named = [h if isinstance(h, str) else next((x for x in h if x), "")
             for p in (settings.answer_law_pattern, settings.answer_law_loose_pattern) if p
             for h in re.findall(p, question)]
    if any(re.sub(r"\s+", "", n) not in bare_text for n in named if n.strip()):
        logger.info("restate dropped (law name not in the utterance): %s", question)
        return "", [], story, follow, lead
    lines = [settings.restate_question_line.format(question=question)]
    if presumption:
        lines.append(settings.restate_presumption_line.format(presumption=presumption))
    raw_terms = data.get(settings.restate_terms_key) or []
    terms = list(dict.fromkeys(t.strip() for t in (raw_terms if isinstance(raw_terms, list) else [raw_terms])
                               if isinstance(t, str) and t.strip() and re.sub(r"\s+", "", t) in bare_text))
    logger.info("restate: %s | %s | terms=%s story=%s follow=%s lead=%s", question, presumption, terms, story, follow, lead)
    return "\n".join(lines), terms[:settings.restate_terms_max], story, follow, lead


async def run_query(
    query: str,
    *,
    mcp: Mcp | None = None,
    model: Model | None = None,
    on_progress: Any = None,
    rounds_max: int | None = None,
    prior: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """질의 하나를 모델-도구 루프로 돌리고 원시 결과 묶음을 돌려준다.

    최상위 성형(코어의 ask 계약 — resolve/candidates/actions 등)은 Task 5 가 한다.
    여기서는 {"answer", "tool_results", "stopped", "usage"} 만 약속한다. `usage` 는
    라운드마다 모델이 낸 `{"tokens_in", "tokens_out"}` 의 합이다 — 이 에이전트가 자기
    모델 루프에서 쓴 토큰은 코어의 budget 에 안 잡히므로(코어는 자신의 ask 호출 한
    번만 센다) 여기서 합산해 성형까지 실어 보내지 않으면 아무도 못 본다.

    `context` 는 호출자가 A2A data 파트로 실어 보낸 것이다. `focus` 가 있으면(= 코어가 이번
    호출에서 원하는 것) 사용자 메시지 **뒤에 한 줄로** 붙인다 — 발화 원문을 고쳐 쓰지 않으면서
    코어가 무엇을 시켰는지 모델이 함께 읽게 하는 자리다(SPEC §2.4). 발화와 같으면 붙이지 않는다.

    `prior` 는 **재개할 때** 이전 턴의 상태를 잇는다 —
    `{"messages": [...], "tool_results": {...}}`. 주면 모델 대화를 이어서 열고 이미
    받은 도구 결과를 그대로 물려준다. 안 주면 매번 처음부터 시작하는데, 그러면
    되묻기에 답해도 모델이 무엇에 대한 답인지 모르고 같은 조회를 다시 한다.

    `mcp`/`model` 을 스스로 만들었을 때만(주입받지 않았을 때만) 끝에서 닫는다 — 호출자가
    준 어댑터는 호출자 소유라 여기서 닫으면 안 된다(테스트의 Fake 들은 aclose 조차
    없다). try/finally 라 정상 답변·라운드 상한·예외, 어느 출구로 나가도 닫힌다 —
    안 그러면 질의마다 MCP 연결 4개가 새 채로 남는다.

    도구 오류는 턴을 죽이지 않는다 — 모델에 되먹여 인자를 고칠 기회를 주고(`errors` 에
    남긴다), 같은 (도구, 인자) 가 `TOOL_RETRY_MAX` 를 넘으면 부르지 않고 반복 차단 문구를
    준다. 연결 불통은 되먹이지 않고 즉시 `stopped="unavailable"` 로 끝낸다(규격 4.3절).

    `stopped` 값: `"answer"`(답을 냈다) · `"ask"`(되묻는다) · `"unavailable"`(도구에 못
    닿았다) · `"rounds"`(라운드 상한) · `"rejected"`(내 일이 아니다 — 실행기가
    `TASK_STATE_REJECTED` 로 종결한다, SPEC 5장). 이 루프는 `"rejected"` 를 스스로 내지
    않는다 — 주입된 `run_query`(관리형 에이전트)가 낸다.
    """
    owns_mcp = mcp is None
    owns_model = model is None
    rounds_max = settings.rounds_max if rounds_max is None else rounds_max

    try:
        mcp = mcp or McpAdapter()
        model = model or ModelAdapter()

        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        prior = prior or {}
        carried = [m for m in (prior.get("messages") or []) if m.get("role") != "system"]
        # 이전 작업은 재개(carried)가 아닐 때만 — 재개는 이미 그 대화를 잇고 있다
        work = "" if carried else previous_work(context)
        usage_total = {"tokens_in": 0, "tokens_out": 0}
        asked = with_focus(query, context)
        # 질문 정리는 새 질문에만 — 재개는 되묻기에 답하는 것이라 앞 대화가 뜻을 정한다
        # 앞 질문을 함께 보인다 — "그럼 어떻게 해"처럼 대상을 생략한 이어지는 질문을 앞 질문의 대상으로 채워 정리하게
        prev_q = last_question(context) if work else ""
        restate_in = asked + (f"\n{settings.restate_prior_line.format(question=prev_q)}"
                              if prev_q and settings.restate_prior_line else "")
        try:
            listed, down = await mcp.list_tools(), None
        except McpUnavailable as exc:
            listed, down = [], exc
        neutral, terms, story_need, follow, lead = (("", [], False, False, "") if carried or down
                                                    else await restated(model, restate_in, usage_total, listed))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *carried,
            *([{"role": "user", "content": work}] if work else []),
            {"role": "user", "content": (f"{settings.current_heading} " if work else "") + asked
             + (f"\n{neutral}" if neutral else "")},
        ]
        tool_results: dict[str, Any] = dict(prior.get("tool_results") or {})
        failures: list[dict[str, Any]] = []
        attempts: dict[tuple[str, str], int] = {}
        marks: dict[str, set[str]] = {}            # 도구별로 지금까지 결과에 나온 근거 표지
        stale: dict[str, int] = {}                 # 새 근거 없이 끝난 호출 수
        calls_made: list[dict[str, Any]] = []      # 작업 기억에 남길 성공한 호출
        pick: tuple[str, str] | None = None        # 모델이 목록에서 골라 다시 불러야 하는 (도구, 목록 키)
        pick_sent_back = False
        mentions: list[str] = []                   # 도구가 답에 꼭 나와야 한다고 표시한 이름(필지 규제 등)

        def done(stopped: str, answer: str = "", **extra: Any) -> dict[str, Any]:
            return {
                "answer": answer, "tool_results": tool_results, "stopped": stopped,
                "usage": dict(usage_total), "errors": list(failures),
                "messages": [m for m in messages if m.get("role") != "system"],
                "calls": list(calls_made),
                **extra,
            }

        if down:
            failures.append(_failure(down.server, down, 1))
            return done("unavailable")
        tools = [*listed, ASK_USER_TOOL, *([story_tool()] if settings.story_prompt else [])]
        known_tools = {t["name"] for t in tools}
        # 정리 단계가 고른 일상어·별칭 — 모델 기억 대신 연계 자료로 확인해 이번 질문 아래에 붙인다
        if terms and settings.restate_term_tool in known_tools:
            found = []
            for word in terms:
                try:
                    res = await mcp.call(settings.restate_term_tool, {settings.restate_term_arg: word})
                except Exception as exc:     # 확인 실패는 질문을 막지 않는다 — 정리 줄만 붙인 채 진행
                    logger.warning("term link failed: %s: %s", word, exc)
                    continue
                failed = not (isinstance(res, Mapping) and res.get("status") == "ok")
                if failed and not (settings.restate_term_failed_pattern
                                   and re.search(settings.restate_term_failed_pattern, word)):
                    continue        # 연계 없는 일반 말은 붙이지 않는다 — 정리한 질문으로 평소대로 찾는다
                found.append(settings.restate_term_line.format(word=word, result=_for_model(res)))
            if found:
                messages[-1] = {**messages[-1], "content": messages[-1]["content"] + "\n" + "\n".join(found)}
        # 연결에 실패한 서버는 실패로 보고한다 — 답이 있으면 COMPLETED + errors
        # (부분 실패, 규격 4.3절).
        report_dead = getattr(mcp, "unavailable", None)
        for label, exc in (report_dead() if callable(report_dead) else {}).items():
            failures.append(_failure(label, McpUnavailable(label, exc), 1))

        cite_checked = force_answer = False
        reviewed = 0
        # 정리 단계가 예시가 필요한 질문으로 보면 요청이 없어도 이야기로 다시 쓴다. 이때는 앞 턴 조회를 다시
        # 돌리지 않는다 — 그건 앞 답을 이야기로 해 달라는 요청(retell_as_story 호출)일 때의 일이다.
        # 이어지는 질문은 판단만으로 바꾸지 않는다 — 앞 턴이 이미 예시를 줬고, 앞 답에서 좁혀 묻는 것이라 사실 답이 필요하다
        story_wanted = replayed = bool(story_need and settings.story_prompt and not (follow and work))
        story_called = False
        story_form: str | None = None
        # 이전 작업 블록의 인용은 근거로 치지 않는다 — 이번에 다시 조회해야 인용할 수 있다
        cite_notes: tuple[str, ...] = (work,) if work else ()
        opened: list[dict[str, Any]] = list(last_calls(context)) if work else []   # 앞 턴에 연 법령도 분야를 켠다
        shown: list[str] = []

        async def replay_last() -> None:
            """앞 턴의 조회를 다시 실행해 결과를 대화에 놓는다. 결과마다 가짜 tool_call 과 짝을 지어 둔다(짝 없는
            tool 메시지는 모델 API 가 거부한다)."""
            replays = [c for c in (last_calls(context) if work else [])
                       if c["tool"] in known_tools and c["tool"] not in RESERVED_TOOLS]
            ids = [f"replay_{i}" for i in range(len(replays))]
            if replays:
                messages.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": cid, "type": "function",
                     "function": {"name": c["tool"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                    for cid, c in zip(ids, replays)]})
            for cid, c in zip(ids, replays):
                key = (c["tool"], args_hash(c["args"]))
                attempts[key] = attempts.get(key, 0) + 1
                try:
                    result = await mcp.call(c["tool"], c["args"])
                except Exception as exc:  # noqa: BLE001 — 다시 실행 실패는 모델이 직접 조회하면 된다
                    content = _for_model({"success": False, "error": str(exc)})
                else:
                    tool_results.pop(c["tool"], None)
                    tool_results[c["tool"]] = result
                    if c not in calls_made:
                        calls_made.append(c)
                    content = _for_model(result)
                messages.append({"role": "tool", "tool_call_id": cid, "name": c["tool"], "content": content})

        # 앞 질문의 대상을 생략한 이어지는 질문 — 앞 턴의 조회를 먼저 다시 실행해 그 근거 위에서 답하게 한다
        if follow and work:
            await replay_last()
            lead = ""           # 앞 턴 조회가 이미 근거를 놓았다
        for round_idx in range(rounds_max):
            # 분야 지침 — 모델이 연 법령·부른 도구에 맞는 지침을 공통 프롬프트 뒤에 붙인다
            on = active_domains(opened + calls_made)
            if on != shown:
                logger.info("domains: %s", on)
                shown = on
            messages[0] = {"role": "system", "content": with_domains(system_prompt, on)}
            if round_idx == rounds_max - 1 and round_idx > 0 and not force_answer and calls_made:
                force_answer = True     # 라운드 상한에서 실패하지 않고 찾은 만큼 답한다
                messages.append({"role": "user", "content": settings.last_round_text})
            if lead and not force_answer:
                # 정리 단계가 고른 도구 — 첫 라운드는 그 도구 하나만 주고 꼭 부르게 한다(인자는 모델이 채운다)
                step = await model.next_step(messages, [t for t in tools if t["name"] == lead], require=True)
                lead = ""
            else:
                step = await model.next_step(messages, [] if force_answer else tools)
            force_answer = False
            step_usage = step.get("usage") or {}
            usage_total["tokens_in"] += step_usage.get("tokens_in") or 0
            usage_total["tokens_out"] += step_usage.get("tokens_out") or 0

            if "answer" in step:
                bad = ungrounded(step["answer"], messages, cite_notes)
                bad += [x for x in mispaired(step["answer"], messages) if x not in bad]
                bad += [x for x in unnamed_laws(step["answer"], messages, cite_notes) if x not in bad]
                bad += [x for x in loose_unnamed_laws(step["answer"], messages, cite_notes) if x not in bad]
                bad += [f"“{x}”" for x in misquoted(step["answer"], messages, cite_notes) if f"“{x}”" not in bad]
                off = rate_unmatched(step["answer"], messages, cite_notes) if settings.answer_rate_check else []
                flips = reversed_conditions(step["answer"], messages) if settings.answer_condition_check else []
                # 점검이 켜져 있으면 조건 대조는 점검에 넘긴다 — 곧바로 되돌리면 오탐이 옳은 문장을 고치게 한다
                review_on = bool(settings.answer_review_prompt) and review_wanted(bool(work), calls_made)
                hinted, flips = (flips, []) if review_on else ([], flips)
                missed = [m for m in mentions if _bare(m) not in _bare(step["answer"])]
                leaks = internal_names(step["answer"], known_tools)
                if missed and not cite_checked and round_idx + 1 < rounds_max:
                    logger.info("answer check: missing mentions=%s", missed)
                # 한 번만 되돌린다 — 다시 걸리면 답은 내되 걸린 인용·수치를 ungrounded 로 남긴다
                if bad or off:
                    logger.info("answer check: cites=%s rates=%s retry=%s", bad, off, not cite_checked)
                if leaks:
                    logger.info("answer check: internal names=%s retry=%s", leaks, not cite_checked)
                if flips:
                    logger.info("answer check: reversed conditions=%s retry=%s", flips, not cite_checked)
                if (bad or off or missed or leaks or flips) and not cite_checked and round_idx + 1 < rounds_max:
                    cite_checked = True
                    messages.append({"role": "assistant", "content": step["answer"]})
                    note = " ".join(([settings.answer_cite_retry_text.format(cites=", ".join(bad))] if bad else [])
                                    + ([settings.answer_rate_retry_text.format(rates=", ".join(off))] if off else [])
                                    + ([settings.mention_retry_text.format(names=", ".join(missed))] if missed else [])
                                    + ([settings.answer_internal_retry_text.format(names=", ".join(leaks))] if leaks else [])
                                    + ([settings.answer_condition_retry_text.format(conds="; ".join(flips))] if flips else [])
                                    + ([settings.answer_retry_tail] if settings.answer_retry_tail else []))
                    cite_notes += (note,)
                    messages.append({"role": "user", "content": note})
                    continue
                # 점검 — 원문과 대조해 고칠 점이 나오면 한 번 되돌린다. 인용 되돌림 뒤에도 걸린 답도 점검한다 — 걸린 인용만
                # 표시하고 내보내면 그 답의 다른 잘못(의제를 거꾸로 씀, 적용 제외 조문 오용)이 그대로 나간다(실측 2026-10-28)
                if (reviewed < settings.answer_review_max and round_idx + 1 < rounds_max
                        and review_wanted(bool(work), calls_made)):
                    reviewed += 1
                    if settings.answer_condition_check and not hinted:
                        hinted = reversed_conditions(step["answer"], messages)
                    if hinted:
                        logger.info("answer check: conditions to review=%s", hinted)
                    problems = await answer_review(model, query, step["answer"], messages, usage_total, hinted)
                    if problems:
                        logger.info("answer review: %s", problems)
                        messages.append({"role": "assistant", "content": step["answer"]})
                        note = settings.answer_review_retry_text.format(problems="\n".join(f"- {p}" for p in problems))
                        note += ("\n" + settings.answer_retry_tail) if settings.answer_retry_tail else ""
                        cite_notes += (note,)
                        messages.append({"role": "user", "content": note})
                        continue
                answer = drop_internal(clean_answer(step["answer"]), leaks)
                bad += [x for x in off if x not in bad]
                if bad and settings.answer_unverified_text:
                    answer += settings.answer_unverified_text.format(cites=", ".join(bad))
                params = [] if bad else await extract_parameters(model, query, answer, messages, usage_total)
                if not (bad and settings.answer_unverified_text) and story_wanted:
                    story = await retell(model, query, answer, messages, cite_notes, usage_total,
                                         story_form, story_laws(tool_results))
                    # 덧붙이는 틀이 있으면 사실 답은 그대로 두고 이야기를 아래에 붙인다(이야기가 사실 답이 되지 않게)
                    answer = (settings.story_append_format.format(answer=answer, story=story)
                              if settings.story_append_format and story != answer else story)
                return done("answer", answer=answer, ungrounded=bad, parameters=params)

            calls = _tool_calls_from_step(step)
            messages.append(_assistant_message(step, calls))
            blocked = 0

            for i, call in enumerate(calls):
                name = call["name"]
                args = call.get("args", {})
                call_id = call.get("id") or f"call_{i}"

                def feed(content: str, *, call_id: str = call_id, name: str = name) -> None:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "name": name, "content": content})

                if name not in known_tools:
                    feed(_UNKNOWN_TOOL_TEMPLATE.format(name=name))
                    continue

                if name == ASK_USER:
                    ask = ask_from(args)
                    if not ask["question"]:
                        feed(_EMPTY_QUESTION_TEXT)
                        continue
                    # 도구가 골라서 다시 부르라고 한 목록을 사용자에게 넘기려 하면 한 번 돌려보낸다
                    if pick and not pick_sent_back:
                        pick_sent_back = True
                        logger.info("ask after pick list from %s — sent back", pick[0])
                        feed(settings.pick_retry_text.format(name=pick[0], key=pick[1]))
                        continue
                    feed(_ASKED_TEXT.format(question=ask["question"]))
                    # 같은 라운드에 남은 호출은 실행하지 않는다 — 짝 없는 tool_calls 를
                    # 남기지 않도록 응답만 채운다(모델 API 는 짝 없는 tool_calls 를 거부한다).
                    for j, later in enumerate(calls[i + 1:], start=i + 1):
                        messages.append({"role": "tool",
                                         "tool_call_id": later.get("id") or f"call_{j}",
                                         "name": later["name"], "content": _NOT_EXECUTED_TEXT})
                    return done("ask", ask=ask)

                if name == STORY:
                    if not story_called:
                        story_called, replayed = True, False
                    story_wanted = True
                    story_form = args.get("form") if isinstance(args, dict) else None
                    logger.info("story requested: form=%s", story_form)
                    feed(settings.story_ack_text)
                    continue

                if bad_arg := ungrounded_arg(args, messages):
                    feed(settings.arg_ungrounded_text.format(key=bad_arg[0], value=bad_arg[1]))
                    continue

                key = (name, args_hash(args))
                attempts[key] = attempts.get(key, 0) + 1
                if attempts[key] > settings.tool_retry_max:
                    feed(REPEAT_TEXT.format(name=name, n=attempts[key]))
                    blocked += 1
                    continue

                if on_progress is not None:
                    fraction = min((round_idx + 1) / rounds_max, 1.0)
                    on_progress(fraction, f"{name} 실행 중")

                try:
                    result = await mcp.call(name, args)
                except Exception as exc:  # noqa: BLE001 — 실패도 결과다. 분류해서 되먹인다
                    failure = _failure(name, exc, attempts[key])
                    failures[:] = [f for f in failures if f["tool"] != name] + [failure]
                    if failure["kind"] == "unavailable":
                        feed(_for_model({"success": False, "error": failure["message"]}))
                        return done("unavailable")
                    feed(_for_model({"success": False, "error": failure["message"]}))
                    continue

                # 성형(shape_result)에는 **자르지 않은 원본**을 남긴다 — 상위
                # 오케스트레이터가 필요로 하는 값(도형·좌표 등)이 뒤쪽에 있을 수 있다.
                # 같은 도구를 다시 부르면 자리를 뒤로 옮긴다 — 키 순서가 곧 마지막 호출
                # 순서다. shape.clarify_source 가 "가장 최근 결과"를 이 순서로 고른다.
                tool_results.pop(name, None)
                tool_results[name] = result
                if (made := {"tool": name, "args": dict(args)}) not in calls_made:
                    calls_made.append(made)
                failures[:] = [f for f in failures if f["tool"] != name]  # 풀린 오류는 안 보인다
                if isinstance(result, Mapping) and isinstance(result.get(settings.pick_key), str):
                    pick = (name, result[settings.pick_key])
                elif pick and pick[0] == name:
                    pick = None
                if isinstance(result, Mapping) and isinstance(result.get(settings.mention_key), list):
                    mentions += [m for m in result[settings.mention_key] if isinstance(m, str) and m not in mentions]
                fed = _for_model(result)
                found = result_marks(fed)
                if found and found <= marks.get(name, set()):
                    stale[name] = stale.get(name, 0) + 1
                    if stale[name] >= settings.tool_retry_max:
                        fed += "\n" + settings.same_result_text.format(name=name, n=stale[name] + 1)
                        blocked += 1
                marks[name] = marks.get(name, set()) | found
                feed(fed)

            # 이번 라운드의 호출이 모두 반복이라 막혔다 — 다음 라운드는 도구 없이 지금까지 조회한 것으로 답하게 한다
            if calls and blocked == len(calls):
                logger.info("all calls blocked as repeats — answer round without tools")
                force_answer = True
                messages.append({"role": "user", "content": settings.repeat_answer_text})

            # 앞 답을 이야기로 — 그 턴의 조회를 런타임이 그대로 다시 실행해 같은 범위의 근거를 놓는다.
            if story_wanted and not replayed:
                replayed = True
                await replay_last()

        return done("rounds")
    finally:
        if owns_mcp and mcp is not None:
            await mcp.aclose()
        if owns_model and model is not None:
            await model.aclose()


# ── 실물 어댑터 ───────────────────────────────────────────────────────────
# run_query 의 테스트는 이 둘 대신 FakeMcp/FakeModel 을 주입해 갈아낀다. 하지만 이
# 둘 자체의 파싱/변환 로직(특히 ModelAdapter.next_step 의 finish_reason 분기)은
# 실물 OpenAI 응답 모양을 흉내낸 가짜 client 를 주입해 tests/test_model_adapter.py
# 가 직접 검증한다 — 네트워크는 절대 열지 않는다. import 시점에도 fastmcp/openai
# 클라이언트를 실제로 만들지 않도록 연결은 지연시킨다(lazy connect).


def _unwrap_call_result(result: Any) -> Any:
    """fastmcp 의 CallToolResult 를 **평범한 dict** 로 편다.

    `.data` 를 그대로 쓰면 안 된다: fastmcp 는 출력 스키마가 있는 도구의 결과를
    동적 생성 타입(예: `Root`)으로 준다 — 값은 맞지만 `Mapping` 이 아니라서
    `isinstance(x, Mapping)` 로 갈라 보는 성형 코드가 통째로 건너뛴다(실측
    2026-08-31: 지도 명령이 하나도 안 나갔고 오류도 안 났다). `structured_content`
    가 같은 내용을 dict 로 주므로 그쪽을 먼저 본다.
    """
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    data = getattr(result, "data", None)
    if data is not None:
        # 타입 객체면 dict 로 편다 — 소비자가 Mapping 으로 다룰 수 있어야 한다.
        for attr in ("model_dump", "dict", "_asdict"):
            fn = getattr(data, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:  # noqa: BLE001 — 못 펴면 원본을 그대로 준다
                    break
        return data
    texts = [c.text for c in getattr(result, "content", []) if getattr(c, "type", None) == "text"]
    if texts:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return None


class McpAdapter:
    """설정이 지정한 MCP 서버들에 붙는다.

    도구 이름이 서버 간에 겹치지 않는다는 전제로, 연결 시점에 이름→서버 표를 한 번
    만들어 그 뒤로는 이름만으로 라우팅한다. 겹치면 나중 서버가 앞을 덮으므로
    `list_tools` 가 그 사실을 로그로 남긴다.
    """

    def __init__(self, urls: dict[str, str] | None = None,
                 allowlist: dict[str, set[str]] | None = None):
        self._urls = urls if urls is not None else settings.servers()
        # 서버별 도구 화이트리스트. 한 서버가 내놓는 도구 중 이 에이전트의 책임에
        # 해당하는 것만 모델에 보인다 — 서버를 나누지 않고 클라이언트에서 거른다.
        self._allow = allowlist if allowlist is not None else settings.allowlist()
        self._clients: dict[str, Any] = {}
        self._tool_server: dict[str, str] = {}
        self._tools: list[dict[str, Any]] | None = None
        self._unavailable: dict[str, BaseException] = {}

    async def _connected_clients(self) -> dict[str, Any]:
        if self._clients or self._unavailable:
            return self._clients
        from fastmcp import Client

        for label, url in self._urls.items():
            client = Client(url)
            try:
                await client.__aenter__()
            except Exception as exc:  # noqa: BLE001 — 어떤 예외든 '못 닿았다'다
                # 한 서버가 죽었다고 나머지까지 버리지 않는다 — 살아 있는 서버의 도구로 답할 수
                # 있는 질문은 답한다(스펙 §9.7). 죽은 서버는 기록해 errors 로 보고한다.
                self._unavailable[label] = exc
                logger.warning(
                    "MCP 서버에 닿지 못했다: %s (%s) — 그 서버 없이 진행한다", label, exc
                )
                continue
            self._clients[label] = client
        if not self._clients:
            label, exc = next(iter(self._unavailable.items()))
            raise McpUnavailable(label, exc)
        return self._clients

    def unavailable(self) -> dict[str, BaseException]:
        """연결에 실패한 서버 {이름: 예외}. `list_tools` 뒤에 뜻이 있다."""
        return dict(self._unavailable)

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._tools is not None:
            return self._tools

        clients = await self._connected_clients()
        collected: list[dict[str, Any]] = []
        for label, client in clients.items():
            for tool in await client.list_tools():
                allow = self._allow.get(label)
                if allow and tool.name not in allow:
                    continue
                prev = self._tool_server.get(tool.name)
                if prev is not None and prev != label:
                    logger.warning(
                        "도구 이름 충돌: %s 가 %s 와 %s 에 모두 있다 — %s 로 라우팅한다",
                        tool.name, prev, label, label,
                    )
                self._tool_server[tool.name] = label
                collected.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                )
        self._tools = collected
        return collected

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if not self._tool_server:
            await self.list_tools()
        server = self._tool_server.get(name)
        if server is None:
            raise KeyError(f"모르는 도구입니다: {name}")
        clients = await self._connected_clients()
        result = await clients[server].call_tool(name, args)
        return _unwrap_call_result(result)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.__aexit__(None, None, None)
        self._clients.clear()
        self._tool_server.clear()
        self._tools = None
        self._unavailable.clear()


#: facts 계약을 따르는 결과에서 모델에 보일 최상위 키. 그 밖(steps·resolve·input·_pwmcp_meta)은
#: 호스트용 원문이라 모델에 가지 않는다 — 성형(shape_result)에는 원본이 그대로 간다.
MODEL_VIEW_KEYS = ("status", "pnu", "facts", "message")


def model_view(result: Any) -> Any:
    """도구 결과의 모델 뷰. `facts` 가 비어 있지 않은 Mapping 이면 그 계약을 따른다.

    코어(ai-map-core)와 같은 규칙이다(`docs/core-model-view.md`): 도구가 모델이 볼
    사실을 스스로 골라 보냈으면 그것만 보인다. 실측 2026-09-01, 토지특성 6단계 원문
    31,275자를 그대로 넣으면 1단계만 닿고 facts 로 좁히면 6단계가 다 닿았다.
    """
    if isinstance(result, Mapping):
        facts = result.get("facts")
        if isinstance(facts, Mapping) and facts:
            return {key: result[key] for key in MODEL_VIEW_KEYS if key in result}
    return result


def _for_model(result: Any) -> str:
    """도구 결과를 모델에 되먹일 문자열로 만든다 — facts 뷰를 취하고, 상한을 넘으면 자른다.

    자른 사실을 문장으로 밝힌다: 모델이 "값이 없다"와 "값이 잘렸다"를 구별해야
    없는 것을 단정하지 않는다.
    """
    text = json.dumps(model_view(result), ensure_ascii=False, default=str)
    limit = settings.tool_result_max_chars
    if limit <= 0 or len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[결과가 {len(text):,}자여서 {limit:,}자까지만 싣습니다. "
        "잘린 뒷부분에 값이 더 있을 수 있으니 '없다'고 단정하지 마세요.]"
    )


def _openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


class ModelAdapter:
    """vLLM(OpenAI 호환) 엔드포인트에 tool-calling 을 건다.

    이 모델은 추론 모델이라 도구를 부르는 턴에서 `message.content` 가 `None` 이고
    사고 과정은 `message.reasoning`(비표준 필드)에 담긴다(실측). 그래서 `content` 의
    참/거짓이 아니라 `finish_reason` 으로만 가른다 — `reasoning` 은 답변에 절대 넣지
    않는다.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        client: Any = None,
    ):
        self._base_url = base_url or settings.vllm_base
        self._api_key = api_key or settings.vllm_api_key or "EMPTY"   # 인증 없는 vLLM 도 키 자리는 비울 수 없다
        self._model = model or settings.vllm_model
        self._temperature = settings.vllm_temperature if temperature is None else temperature
        # client 를 주입하면(테스트) 그 client 는 우리가 열지 않았으니 우리가 닫지도
        # 않는다 — _owns_client 로 기억해 둔다.
        self._client: Any = client
        self._owns_client = client is None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()

    async def next_step(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], require: bool = False
    ) -> dict[str, Any]:
        """`require` 면 준 도구 가운데 하나를 꼭 부르게 한다(tool_choice=required)."""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": messages,
        }
        if tools:   # 도구 없는 호출(이야기 단계)은 tools 를 아예 보내지 않는다
            kwargs["tools"] = [_openai_tool(t) for t in tools]
            if require:
                kwargs["tool_choice"] = "required"
        # 추론 강도는 제공자가 지원할 때만 의미가 있다 — 빈 값이면 아예 안 보낸다.
        # 보내면 지원하지 않는 제공자가 400 으로 거부할 수 있다.
        if settings.reasoning_effort:
            kwargs["reasoning_effort"] = settings.reasoning_effort
        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        usage = _usage_from_response(response)

        if choice.finish_reason == "tool_calls" and message.tool_calls:
            calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": _parse_tool_arguments(tc.function.arguments),
                }
                for tc in message.tool_calls
            ]
            raw_assistant_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ],
            }
            return {"tools": calls, "raw_assistant_message": raw_assistant_message, "usage": usage}

        # finish_reason == "stop" (또는 그 밖의 종료) — content 가 최종 답이다.
        # message.reasoning 은 절대 여기 섞지 않는다: 사용자에게 보일 텍스트가 아니다.
        return {"answer": message.content or "", "usage": usage}


def _usage_from_response(response: Any) -> dict[str, int]:
    """vLLM/openai 응답의 `usage.prompt_tokens`/`completion_tokens` 를 옮긴다.

    둘 다 없을 수 있다(비표준 엔드포인트, 스트리밍 중간 응답 등) — 절대 죽지 않고
    0 으로 채운다. `getattr` 기본값이라 `usage` 자체가 없는 응답에도 안전하다.
    """
    usage = getattr(response, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", None) if usage is not None else None
    tokens_out = getattr(usage, "completion_tokens", None) if usage is not None else None
    return {"tokens_in": tokens_in or 0, "tokens_out": tokens_out or 0}


def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """`function.arguments` 는 JSON 문자열이지 dict 가 아니다 — 직접 파싱한다."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
