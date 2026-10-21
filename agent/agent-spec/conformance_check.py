#!/usr/bin/env python3
"""에이전트 자가 점검기.

A2A 규격을 옮겨 적은 검사다. **적합성 보증이 아니다** — 최종 근거는
`reference/a2a.proto` 다. 눈으로 못 잡는 것을 잡는 용도다: 직렬화 규칙, 상태 전이,
되묻기 루프가 닫히는지.

**층을 갈라 판정한다.**

  A2A (SPEC 2장·3장) — 표준이 정한 것. 어기면 **불합격**, 종료 코드 1.
  그 밖 (4장 프로파일 · 6장 설계 지침) — 안 지켜도 **A2A 적합**이고 종료 코드에
  영향이 없다. 안내만 낸다 — 우리 관례로 남의 에이전트를 떨어뜨리지 않는다.

의존성 없음(표준 라이브러리만). 어느 언어로 만든 에이전트든 검사한다.

    python3 conformance_check.py http://localhost:8080
    python3 conformance_check.py https://agent.example.com --probe "재고 있어?"

--probe 를 주면 실제로 발화를 보내 왕복까지 확인한다. 모델을 부르므로 비용이
든다 — 안 주면 카드와 오류 처리만 정적으로 검사한다.

종료 코드: 0 통과, 1 실패(MUST 위반), 2 검사 자체가 불가(연결 실패 등).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

CARD_PATH = "/.well-known/agent-card.json"
A2A_JSON = "application/a2a+json"
A2A_VERSION = "1.0"

REQUIRED_CARD_FIELDS = [
    "name", "description", "version", "supportedInterfaces",
    "capabilities", "defaultInputModes", "defaultOutputModes", "skills",
]
# 규격이 이름을 준 바인딩 셋. 그 밖에 커스텀 바인딩은 URI 로 식별한다(규격 §5.8).
STD_BINDINGS = {"HTTP+JSON", "JSONRPC", "GRPC"}
STATES = {
    "TASK_STATE_UNSPECIFIED", "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING",
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED",
}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")
SNAKE_RE = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")

#: (level, ref, message, layer)
RESULTS: list[tuple[str, str, str, str]] = []

#: 절 번호 → 층. **A2A 실패만 불합격이다.** 프로파일 미준수를 불합격으로 만들면
#: 표준을 지킨 남의 에이전트가 부당하게 떨어진다.
_LAYERS = {"2": "a2a", "3": "a2a",          # A2A 규격이 정한 것
           "6": "guide", "8": "guide",      # Anthropic 지침
           "4": "profile", "5": "other", "7": "other", "9": "other"}


def _layer(ref: str) -> str:
    if ref.startswith("A2A 규격"):
        return "a2a"
    return _LAYERS.get(ref.lstrip("§").rstrip("절").split(".")[0], "a2a")


def header(headers, name: str, default: str = "") -> str:
    """HTTP 헤더는 대소문자를 가리지 않는다(RFC 9110).

    서버마다 다르게 보낸다 — uvicorn 은 소문자, 파이썬 http.server 는 준 그대로.
    `dict.get("Content-Type")` 로 찾으면 절반은 못 찾는다.
    """
    want = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == want:
            return value
    return default


def ok(ref, msg):    RESULTS.append(("PASS", ref, msg, _layer(ref)))
def fail(ref, msg):  RESULTS.append(("FAIL", ref, msg, _layer(ref)))
def warn(ref, msg):  RESULTS.append(("WARN", ref, msg, _layer(ref)))
def info(ref, msg):  RESULTS.append(("INFO", ref, msg, _layer(ref)))


def request(url, *, method="GET", body=None, timeout=30, version=None):
    """(status, headers, parsed_json_or_text) 를 돌려준다. 예외를 상태로 바꾼다."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", A2A_JSON)
    req.add_header("Accept", A2A_JSON + ", application/json")
    # 클라이언트는 요청마다 버전을 보내야 한다(A2A 규격 3.6.1절). 빈 값은
    # 서버가 0.3 으로 해석하므로, 안 보내면 구버전으로 처리된다.
    req.add_header("A2A-Version", version or A2A_VERSION)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), _parse(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, dict(e.headers or {}), _parse(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 연결 자체가 안 됨. 호출자가 상태 0 으로 갈라 본다.
        return 0, {}, str(getattr(e, "reason", e))


def _parse(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# 규격이 정한 필드가 아니라 **자유 JSON**인 자리. camelCase 규칙이 적용되지 않는다 —
# 여기 키는 스킬 id·확장 URI·도메인 데이터라 snake_case 여도 적합하다.
FREE_FORM = {"params", "metadata", "data"}


def snake_keys(obj, path="") -> list[str]:
    """snake_case 로 직렬화된 키를 찾는다 — 2.3절 위반."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("http"):
                continue                       # 확장 URI 는 키가 URI 다
            if isinstance(k, str) and SNAKE_RE.match(k):
                found.append(f"{path}.{k}" if path else k)
            if k in FREE_FORM:
                continue                       # 그 아래는 규격의 관할이 아니다
            found += snake_keys(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += snake_keys(v, f"{path}[{i}]")
    return found


# ────────────────────────────────────────────── 카드

def api_base_from(card, origin):
    """API 요청을 보낼 기준 URL. **카드가 정한다** — origin 이 아니다.

    well-known 은 origin 당 하나라 카드는 origin 에서 읽지만(규격 §8.2), 실제
    엔드포인트는 `supportedInterfaces[].url` 이 가리키는 곳에 있다(규격 2.4.1절).
    경로 접두사(`https://host/agents/catalog`)나 프록시 주소가 붙을 수 있다.

    다만 배포 중에는 카드에 최종 주소가 아직 안 박힌 경우가 흔하다. 카드가 준
    주소에 닿지 않으면 origin 으로 되돌아가고 경고를 남긴다 — 검사를 통째로
    포기하는 것보다 낫다.
    """
    if not isinstance(card, dict):
        return origin
    url = next((str(i.get("url", "")).rstrip("/")
                for i in (card.get("supportedInterfaces") or [])
                if isinstance(i, dict) and i.get("protocolBinding") == "HTTP+JSON"
                and i.get("url")), "")
    if not url or url == origin:
        return origin
    status, _, _ = request(url + "/tasks/probe-" + uuid.uuid4().hex, timeout=10)
    if status == 0:
        warn("2.4.1절", f"카드가 선언한 주소에 닿지 않아 {origin} 으로 검사한다: {url} "
                       "— 배포 전이라면 정상이지만, 오케스트레이터는 이 주소로 보낸다")
        return origin
    info("2.4.1절", f"API 기준 URL 을 카드에서 얻었다: {url}")
    return url


def check_card(base):
    status, headers, card = request(base + CARD_PATH)
    if status != 200:
        fail("2.1절", f"{CARD_PATH} 가 {status} 를 냈다 (200 이어야 한다)")
        return None
    if not isinstance(card, dict):
        fail("2.4절", "카드가 JSON 객체가 아니다")
        return None
    ok("2.1절", "에이전트 카드에 인증 없이 닿는다")

    missing = [f for f in REQUIRED_CARD_FIELDS if f not in card]
    if missing:
        fail("2.4절", f"카드 필수 필드 누락: {', '.join(missing)}")
    else:
        ok("2.4절", "카드 필수 8필드가 모두 있다")

    if snakes := snake_keys(card):
        fail("2.3절", f"카드에 snake_case 키가 있다 (camelCase 여야 한다): {', '.join(snakes[:5])}")
    else:
        ok("2.3절", "카드 필드명이 camelCase 다")

    ifaces = card.get("supportedInterfaces")
    if not isinstance(ifaces, list) or not ifaces:
        fail("2.4.1절", "supportedInterfaces 가 비었다 (최소 1개)")
    else:
        for i, itf in enumerate(ifaces):
            if not isinstance(itf, dict):
                fail("2.4.1절", f"supportedInterfaces[{i}] 가 객체가 아니다"); continue
            for f in ("url", "protocolBinding", "protocolVersion"):
                if not itf.get(f):
                    fail("2.4.1절", f"supportedInterfaces[{i}].{f} 가 없다")
            b = itf.get("protocolBinding")
            if b and b not in STD_BINDINGS:
                if str(b).startswith(("http://", "https://")):
                    info("2.4.1절", f"커스텀 바인딩을 선언했다: {b} "
                                   "— 이 검사기는 HTTP+JSON 만 검사한다")
                else:
                    fail("2.4.1절", f"protocolBinding 값이 잘못됐다: {b!r} "
                                   f"— 표준 {sorted(STD_BINDINGS)} 중 하나이거나 "
                                   "커스텀 바인딩 URI 여야 한다(규격 §5.8)")
            u = str(itf.get("url", ""))
            if u.startswith(("http://localhost", "http://127.0.0.1")):
                warn("7.3절", f"supportedInterfaces[{i}].url 이 로컬 주소다: {u} "
                             "— 밖에서 닿는 주소여야 한다(PUBLIC_URL)")
        if all(isinstance(x, dict) for x in ifaces):
            ok("2.4.1절", f"supportedInterfaces {len(ifaces)}개가 형식을 지킨다")
        if not any(isinstance(x, dict) and x.get("protocolBinding") == "HTTP+JSON"
                   for x in ifaces):
            warn("2.4.1절", "HTTP+JSON 인터페이스를 선언하지 않았다 — "
                           "이 검사기는 그 바인딩만 검사하므로 이하 결과가 의미 없을 수 있다")

    caps = card.get("capabilities")
    if not isinstance(caps, dict):
        fail("2.4.2절", "capabilities 가 객체가 아니다")
        caps = {}
    else:
        ok("2.4.2절", f"capabilities 선언: streaming={caps.get('streaming')} "
                     f"pushNotifications={caps.get('pushNotifications')}")

    skills = card.get("skills")
    if not isinstance(skills, list) or not skills:
        fail("2.4.3절", "skills 가 비었다 (최소 1개)")
    else:
        bad = []
        for s in skills:
            if not isinstance(s, dict):
                bad.append("(객체 아님)"); continue
            for f in ("id", "name", "description", "tags"):
                if f not in s:
                    bad.append(f"{s.get('id', '?')}.{f}")
        if bad:
            fail("2.4.3절", f"스킬 필수 필드 누락: {', '.join(bad[:6])}")
        else:
            ok("2.4.3절", f"스킬 {len(skills)}개가 필수 필드를 갖췄다")
        for s in skills:
            if isinstance(s, dict) and not s.get("examples"):
                warn("4장.4.2", f"스킬 {s.get('id')!r} 에 examples 가 없다")

    check_hints(card, caps, skills if isinstance(skills, list) else [])
    check_description(card, skills if isinstance(skills, list) else [])
    return card


def check_hints(card, caps, skills):
    exts = caps.get("extensions") or []
    hints = None
    for e in exts:
        if isinstance(e, dict) and "tool-hints" in str(e.get("uri", "")):
            hints = (e.get("params") or {}).get("skills")
            break
    if hints is None:
        fail("4.1.1절", "tool-hints 확장이 없다 — 이 에이전트는 '쓰기'로 취급되어 "
                       "병렬 실행에서 빠진다. 오류가 안 나는 문제라 반드시 확인하라")
        return
    ids = {s.get("id") for s in skills if isinstance(s, dict)}
    if missing := ids - set(hints):
        fail("4.1.1절", f"tool-hints 에 빠진 스킬: {', '.join(sorted(map(str, missing)))}")
        return
    ro = [k for k, v in hints.items() if isinstance(v, dict) and v.get("readOnly")]
    verdict = "read (병렬 가능)" if len(ro) == len(hints) else "mutate (직렬)"
    ok("4.1.1절", f"tool-hints 가 스킬 {len(hints)}개를 덮는다 → 집계 결과 {verdict}")
    _check_ext_domain(exts)


#: 문서·샘플의 자리표시자. 그대로 쓰면 상대가 확장을 못 읽는다.
PLACEHOLDER_HOSTS = ("example.org", "example.com", "example.invalid",
                     "<도메인>", "your-domain", "example.net")


def _check_ext_domain(exts) -> None:
    """확장 URI 의 도메인이 정해졌는지. 안 정하면 **조용히** 무시된다(4.1절)."""
    for e in exts:
        uri = str(e.get("uri", "")) if isinstance(e, dict) else ""
        if any(h in uri for h in PLACEHOLDER_HOSTS):
            fail("4.1절", f"확장 URI 가 자리표시자입니다: {uri} "
                          "— 붙을 플랫폼이 공표한 값이나 소유 도메인으로 바꾸세요. "
                          "그대로 두면 상대가 못 읽고, 오류도 안 납니다")
            return
    ok("4.1절", "확장 URI 의 도메인이 정해져 있다")


NEGATIVE = ("아니다", "아닙니다", "하지 않는다", "않습니다", "제외", "not ", "does not")


def check_description(card, skills):
    desc = str(card.get("description", ""))
    if len(desc) < 20:
        fail("6.2절", "description 이 너무 짧다 — 라우팅의 유일한 근거다")
    elif not any(n in desc for n in NEGATIVE):
        warn("6.2절", "description 에 배제 문장('…는 이 에이전트가 아니다')이 없어 보인다 "
                       "— 오케스트레이터가 엉뚱한 발화를 보낸다")
    else:
        ok("6.2절", "description 에 경계(배제 문장)가 있다")


# ────────────────────────────────────────────── 오류 처리

def check_version(base) -> None:
    """모르는 `A2A-Version` 을 거절하는지 (A2A 규격 3.6.2절).

    안 보면 모든 요청을 구버전(0.3)으로 처리하게 된다 — 규격이 빈 값을 그렇게
    해석하라고 정했기 때문이다.
    """
    status, _, body = request(base + "/tasks/probe-" + uuid.uuid4().hex,
                              version="99.0")
    if status == 0:
        return
    title = body.get("title") if isinstance(body, dict) else ""
    if status == 400 and title == "VersionNotSupportedError":
        ok("2.2절", "모르는 A2A-Version 을 VersionNotSupportedError 로 거절한다")
    elif status == 404:
        warn("2.2절", "A2A-Version 을 안 보는 것으로 보인다 — 모르는 버전(99.0)도 그냥 "
                      "처리했다. 빈 값이 0.3 으로 해석되므로 헤더를 읽지 않으면 "
                      "구버전으로 처리하게 된다(A2A 규격 3.6절)")
    else:
        info("2.2절", f"모르는 버전에 HTTP {status} 를 냈다 (400 + "
                      "VersionNotSupportedError 가 규격이다)")


def check_errors(base, caps):
    status, headers, body = request(base + "/tasks/nonexistent-" + uuid.uuid4().hex)
    if status == 404:
        ok("2.5절", "없는 taskId 에 404 를 낸다")
        ct = header(headers, "Content-Type")
        if "problem+json" not in ct:
            warn("2.7절", f"오류 Content-Type 이 problem+json 이 아니다: {ct!r}")
        else:
            ok("2.7절", "오류가 problem+json 으로 온다")
    else:
        fail("2.5절", f"없는 taskId 에 {status} 를 냈다 (404 여야 한다)")

    if caps.get("streaming") is not True:
        status, _, _ = request(base + "/message:stream", method="POST",
                               body={"message": {"messageId": uuid.uuid4().hex,
                                                 "role": "ROLE_USER",
                                                 "parts": [{"text": "ping"}]}})
        if status == 400:
            ok("2.1절", "streaming 미선언 상태에서 /message:stream 이 400 을 낸다")
        elif status in (404, 405, 501):
            warn("2.1절", f"/message:stream 이 {status} 다 — 규격은 400 "
                         "(UnsupportedOperationError) 을 요구한다")
        else:
            fail("2.1절", f"streaming: false 인데 /message:stream 이 {status} 를 냈다")


# ────────────────────────────────────────────── 왕복

def _pick_answer(candidates) -> str:
    """첫 후보를 가리키는 답변 문자열. 도메인을 모르므로 값을 그대로 이어 붙인다."""
    first = candidates[0]
    if isinstance(first, dict):
        vals = [str(v) for v in first.values() if isinstance(v, (str, int, float))]
        return " ".join(vals) if vals else json.dumps(first, ensure_ascii=False)
    return str(first)


def _check_clarify(base, task, timeout):
    """되묻기를 **답까지 보내서** 루프가 닫히는지 본다.

    형식만 보는 것으로는 부족하다. 실측 사례에서 통과율 0% 를 만든 것은 형식이
    아니라 **루프가 안 닫히는 것**이었다 — 후보를 골라 보내도 같은 질문이 돌아왔다.
    """
    tid, cid = task.get("id"), task.get("contextId")
    msg = (task.get("status") or {}).get("message") or {}

    if not msg.get("parts"):
        fail("2.6절", "INPUT_REQUIRED 인데 status.message 에 질문이 없다")
        return
    if msg.get("role") != "ROLE_AGENT":
        fail("2.6절", f"되묻기 메시지의 role 이 {msg.get('role')!r} 다 (ROLE_AGENT 여야 한다)")
        return
    if not msg.get("taskId") or not msg.get("contextId"):
        fail("2.6절", "되묻기 메시지에 taskId/contextId 가 없다")
        return
    ok("2.6절", "되묻기가 형식을 지킨다")
    question = " ".join(p.get("text", "") for p in msg.get("parts", []))

    cands_art = next((a for a in (task.get("artifacts") or [])
                      if isinstance(a, dict) and a.get("name") == "candidates"), None)
    if not cands_art:
        warn("2.6절", "되묻기인데 candidates 아티팩트가 없다 — 사용자가 무엇을 고를지 알 수 없다")
        return
    data = (cands_art.get("parts") or [{}])[0].get("data")
    if not isinstance(data, list) or not data:
        fail("2.6절", "candidates 아티팩트의 data 가 목록이 아니다")
        return
    if len(data) > 1:
        keys = [json.dumps(d, sort_keys=True, ensure_ascii=False) for d in data]
        if len(set(keys)) < len(keys):
            fail("2.6절", "되묻기 후보에 서로 같은 항목이 있다 — 사용자가 골라도 구별되지 않는다")
            return
        ok("2.6절", f"되묻기 후보 {len(data)}개가 서로 구별된다")

    # ── 여기부터가 진짜 검사: 답을 보내 루프가 닫히는지
    answer = _pick_answer(data)
    status, _, res = request(base + "/message:send", method="POST", timeout=timeout,
                             body={"message": {"messageId": str(uuid.uuid4()),
                                               "role": "ROLE_USER",
                                               "taskId": tid, "contextId": cid,
                                               "parts": [{"text": answer}]}})
    if status != 200:
        fail("2.6절", f"되묻기에 답했는데 {status} 다: {str(res)[:140]}")
        return
    resumed = (res or {}).get("task") if isinstance(res, dict) else None
    if not isinstance(resumed, dict):
        fail("2.6절", "재개 응답에 task 가 없다")
        return
    if resumed.get("id") != tid:
        fail("2.6절", f"재개가 **새 태스크를 만들었다**: {tid} → {resumed.get('id')} "
                     "— 같은 태스크가 이어져야 한다")
        return
    ok("2.6절", "재개가 같은 태스크를 이어간다")

    final2, last2 = _poll(base, tid, timeout)
    if final2 is None:
        fail("2.6절", "재개 후 시한 안에 끝나지 않았다")
        return

    if final2 == "TASK_STATE_INPUT_REQUIRED":
        again = " ".join(p.get("text", "") for p in
                         ((last2.get("status") or {}).get("message") or {}).get("parts", []))
        if again.strip() == question.strip():
            fail("2.6절", "후보를 골라 답했는데 **똑같은 질문이 돌아왔다** — 루프가 닫히지 않는다. "
                         "재개 상태(원래 요청·직전 질문·후보 목록)를 들고 있는지 보라")
        else:
            warn("2.6절", "재개 후 다시 되물었다 — 질문은 달라졌으니 진전은 있다")
    else:
        ok("2.6절", f"되묻기 루프가 닫혔다: INPUT_REQUIRED → {final2.replace('TASK_STATE_', '')}")

    history = last2.get("history") or []
    if len(history) >= 2:
        ok("2.6절", f"Task.history 에 왕복이 남았다 ({len(history)}턴)")
    else:
        warn("2.6절", "Task.history 가 비었거나 짧다 — 관찰자가 대화를 재구성할 수 없다")


def check_blocking(base, probe, timeout):
    """`returnImmediately` 를 빼면 종결·중단까지 **막고 기다려야** 한다(규격 A2A 규격 3.5절)."""
    status, _, res = request(base + "/message:send", method="POST", timeout=timeout,
                             body={"message": {"messageId": str(uuid.uuid4()),
                                               "role": "ROLE_USER",
                                               "parts": [{"text": probe}]}})
    if status != 200:
        fail("A2A 규격 3.5절", f"블로킹 모드에서 {status} 를 냈다: {str(res)[:140]}")
        return
    task = (res or {}).get("task") if isinstance(res, dict) else None
    if not isinstance(task, dict):
        info("A2A 규격 3.5절", "블로킹 모드가 Message 를 돌려줬다 — 규격이 허용하는 형태다")
        return
    state = (task.get("status") or {}).get("state")
    if state in TERMINAL or state == "TASK_STATE_INPUT_REQUIRED":
        ok("A2A 규격 3.5절", f"블로킹 모드가 끝난 상태로 돌아왔다 ({state.replace('TASK_STATE_', '')})")
    else:
        fail("A2A 규격 3.5절", f"블로킹 모드인데 {state} 로 즉시 돌아왔다 "
                     "— returnImmediately 가 없으면 종결·중단까지 기다려야 한다")


def _poll(base, tid, timeout):
    """종결·중단까지 폴링. (마지막 상태, 태스크) 또는 (None, 마지막 응답)."""
    deadline, last = time.time() + timeout, {}
    while time.time() < deadline:
        status, _, t = request(f"{base}/tasks/{tid}", timeout=timeout)
        if status != 200 or not isinstance(t, dict):
            return None, last
        last = t
        state = (t.get("status") or {}).get("state")
        if state in TERMINAL or state == "TASK_STATE_INPUT_REQUIRED":
            return state, t
        time.sleep(1.0)
    return None, last


def check_roundtrip(base, probe, timeout):
    body = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER",
                        "parts": [{"text": probe}]},
            "configuration": {"returnImmediately": True}}
    status, _, res = request(base + "/message:send", method="POST", body=body, timeout=timeout)
    if status != 200:
        fail("2.1절", f"/message:send 가 {status} 를 냈다: {str(res)[:160]}")
        return
    if not isinstance(res, dict) or "task" not in res:
        fail("A2A 규격 3.5절", f"응답에 task 가 없다: {str(res)[:160]}")
        return
    task = res["task"]
    tid = task.get("id")
    if not tid:
        fail("2.5절", "task.id 가 없다")
        return
    ok("2.1절", f"/message:send 가 태스크를 만들었다 (id={tid})")
    if snakes := snake_keys(task):
        fail("2.3절", f"태스크에 snake_case 키: {', '.join(snakes[:5])}")

    deadline, last, trail = time.time() + timeout, None, []
    while time.time() < deadline:
        status, _, t = request(f"{base}/tasks/{tid}", timeout=timeout)
        if status != 200 or not isinstance(t, dict):
            fail("2.1절", f"GET /tasks/{tid} 가 {status} 를 냈다"); return
        st = (t.get("status") or {}).get("state")
        if st not in STATES:
            fail("2.3절", f"state 값이 잘못됐다: {st!r} — TASK_STATE_* 문자열이어야 한다"); return
        if not trail or trail[-1] != st:
            trail.append(st)
        ts = (t.get("status") or {}).get("timestamp")
        if ts and not TS_RE.match(str(ts)):
            fail("2.3절", f"timestamp 형식이 틀렸다: {ts!r} (…Z 여야 한다)")
        last = t
        if st in TERMINAL or st == "TASK_STATE_INPUT_REQUIRED":
            break
        time.sleep(1.0)

    if last is None:
        fail("2.1절", "폴링이 아무 응답도 못 받았다"); return
    final = (last.get("status") or {}).get("state")
    if final not in TERMINAL and final != "TASK_STATE_INPUT_REQUIRED":
        fail("2.5절", f"{timeout}초 안에 종결·중단에 닿지 못했다 (마지막 상태 {final})"); return
    ok("2.5절", "상태 전이: " + " → ".join(s.replace("TASK_STATE_", "") for s in trail))

    names = [a.get("name") for a in (last.get("artifacts") or []) if isinstance(a, dict)]
    if final == "TASK_STATE_COMPLETED":
        if "answer" in names:
            ok("4.2절", f"아티팩트: {', '.join(map(str, names))}")
        else:
            fail("4.2절", f"COMPLETED 인데 answer 아티팩트가 없다 (있는 것: {names})")
    for a in (last.get("artifacts") or []):
        if isinstance(a, dict) and not a.get("artifactId"):
            fail("3.4절", f"아티팩트 {a.get('name')!r} 에 artifactId 가 없다")

    msg = (last.get("status") or {}).get("message")
    if final == "TASK_STATE_INPUT_REQUIRED":
        _check_clarify(base, last, timeout)

    if final == "TASK_STATE_FAILED":
        text = " ".join(p.get("text", "") for p in (msg or {}).get("parts", []))
        if len(text.strip()) < 10:
            fail("4.3절", "FAILED 인데 status.message 에 사람이 읽을 사유가 없다")
        else:
            ok("4.3절", "실패 사유가 설명돼 있다")

    # 종결 태스크에 메시지 → 400
    if final in TERMINAL:
        status, _, _ = request(base + "/message:send", method="POST", timeout=timeout,
                               body={"message": {"messageId": str(uuid.uuid4()),
                                                 "role": "ROLE_USER",
                                                 "taskId": tid,
                                                 "contextId": last.get("contextId"),
                                                 "parts": [{"text": "다시"}]}})
        if status == 400:
            ok("2.5절", "종결 태스크에 온 메시지를 400 으로 거절한다")
        else:
            fail("2.5절", f"종결 태스크에 메시지를 보냈더니 {status} 다 (400 이어야 한다)")

    ext = last.get("metadata") or {}
    if any("usage" in str(k) for k in ext):
        ok("4.1.2절", "usage 확장이 실렸다")
    else:
        warn("4.1.2절", "Task.metadata 에 usage 가 없다 — 토큰 사용량을 아무도 못 본다")


# ────────────────────────────────────────────── 진입점

def main() -> int:
    ap = argparse.ArgumentParser(description="에이전트 적합성 검사 (SPEC.md §2)")
    ap.add_argument("base_url", help="에이전트 기준 URL (예: http://localhost:8080)")
    ap.add_argument("--probe", help="왕복 검사에 쓸 발화. 주면 모델을 실제로 부른다")
    ap.add_argument("--timeout", type=int, default=60, help="왕복 대기 상한(초). 기본 60")
    args = ap.parse_args()
    origin = args.base_url.rstrip("/")

    print(f"검사 대상: {origin}\n")
    status, _, _ = request(origin + CARD_PATH, timeout=10)
    if status == 0:
        print(f"연결 실패: {origin} 에 닿지 않는다", file=sys.stderr)
        return 2
    card = check_card(origin)
    caps = (card or {}).get("capabilities") or {}
    # 카드는 origin 에서 읽었고, 이제부터의 API 요청은 카드가 정한 주소로 간다.
    api = api_base_from(card, origin)
    check_version(api)
    check_errors(api, caps)
    if args.probe:
        check_roundtrip(api, args.probe, args.timeout)
        check_blocking(api, args.probe, args.timeout)
    else:
        info("—", "--probe 를 주지 않아 왕복 검사를 건너뛰었다 (카드·오류만 검사)")

    width = max((len(r) for _, r, _, _ in RESULTS), default=6)
    icon = {"PASS": "✓", "FAIL": "✗", "WARN": "!", "INFO": "·"}
    for title, layer in (("A2A 적합성 — 표준이 정한 것 (SPEC 2장·3장)", "a2a"),
                         ("설계 지침 — Anthropic (SPEC 6장·8장)", "guide"),
                         ("이 플랫폼의 프로파일 — 표준이 아니다 (SPEC 4장)", "profile")):
        rows = [r for r in RESULTS if r[3] == layer]
        if not rows:
            continue
        print(f"\n  ── {title}")
        for level, ref, msg, _ in rows:
            print(f"    {icon[level]} {level:<4} {ref:<{width}}  {msg}")

    def count(level, layer=None):
        return sum(1 for l, _, _, y in RESULTS if l == level and (layer is None or y == layer))

    a2a_fail = count("FAIL", "a2a")
    print(f"\n  A2A 적합성 : 통과 {count('PASS', 'a2a')} · 실패 {a2a_fail}")
    for label, layer in (("설계 지침   ", "guide"), ("프로파일    ", "profile")):
        miss = count("FAIL", layer) + count("WARN", layer)
        if miss:
            print(f"  {label}: 미준수 {miss} — **A2A 적합성과 무관하다**")

    if a2a_fail:
        print("\n  A2A 적합하지 않다. FAIL 항목의 절 번호를 SPEC.md 2장·3장 에서 찾아 고쳐라.")
        return 1
    tail = ""
    if count("FAIL", "profile") or count("FAIL", "guide"):
        tail = "  다만 이 플랫폼의 부가 기능은 못 쓴다(SPEC 4장)."
    print("\n  A2A 적합하다." + tail)
    return 0





if __name__ == "__main__":
    sys.exit(main())
