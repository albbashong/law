"""SPEC §2 를 지키는 최소 에이전트 — **샘플이다.**

규격이 아니라 "이 정도면 A2A 적합하다" 를 보여 주는 한 구현이다. 여기 나오는 도메인
(상품 카탈로그)·필드 이름·응답 문구는 전부 예시이고, 지켜야 하는 것은 §2 뿐이다.

표준 라이브러리만 쓴다.

**참조 구현이지 프로덕션 코드가 아니다.** 모델도 도구도 부르지 않고, 인증이 없고,
태스크를 메모리에만 둔다. 그럼에도 **되묻기 재개는 진짜로 구현한다** — 그게 이
규격에서 가장 비싼 부분이고(§2.7 재개), 흉내만 내면 참조 구현이 아니기 때문이다.

  python3 stub_agent.py <port> [--clarify] [--break-hints] [--prefix /경로]

  --clarify       첫 요청에 되묻고, 답을 받으면 그 후보로 완료한다
  --break-hints   tool-hints 확장을 뺀다 (검사기가 잡아야 한다)
  --fake-resume   재개 상태를 안 들고 있는 흉내. 답해도 같은 질문이 돌아온다
                  — 실측에서 통과율 0% 를 만든 실패 모드다. 검사기가 잡아야 한다
  --prefix        API 를 경로 접두사 아래에 둔다. well-known 은 origin 에 남는다

표준 라이브러리만 쓴다. Python 3.9 이상이면 돈다(여기서는 3.13 으로 검증했다).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
CLARIFY = "--clarify" in sys.argv or "--fake-resume" in sys.argv
FAKE_RESUME = "--fake-resume" in sys.argv
BREAK_HINTS = "--break-hints" in sys.argv
PREFIX = (sys.argv[sys.argv.index("--prefix") + 1].rstrip("/")
          if "--prefix" in sys.argv else "")
ORIGIN = f"http://localhost:{PORT}"
BASE = ORIGIN + PREFIX
# 확장 URI 의 뿌리. **이 값은 정해야 합니다** — SPEC 4.1절 참고.
# 붙을 플랫폼이 공표한 값을 쓰거나, 자기 프로파일이면 소유 도메인으로 바꿉니다.
# 예시 그대로 두면 상대가 확장을 못 읽습니다(오류는 안 나고 무시됩니다).
EXT = "https://example.invalid/ext"   # ← 반드시 바꿀 것

#: 이 서버가 말하는 버전. Major.Minor 만 가린다. 빈 값은 규격상 0.3 이다(3.6.2절).
SUPPORTED_VERSIONS = {"1.0"}

TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
            "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}

CATALOG = [
    {"sku": "A-1023", "label": "파란 머그컵 350ml", "stock": 12},
    {"sku": "A-1024", "label": "흰 머그컵 350ml", "stock": 3},
    {"sku": "B-2001", "label": "보온 머그컵 400ml", "stock": 0},
]

TASKS: dict[str, dict] = {}
#: 태스크별 **재개 상태**(§2.7). 프로토콜에 안 나오는 에이전트 내부 저장소다.
#: 태스크와 수명을 같이한다 — 안 그러면 샌다.
LOOP: dict[str, dict] = {}
LOCK = threading.Lock()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def mid():
    return "m_" + uuid.uuid4().hex[:12]


def card():
    caps = {
        "streaming": False, "pushNotifications": False, "extendedAgentCard": False,
        "extensions": [
            {"uri": f"{EXT}/progress/v1", "required": False, "description": "진행률"},
            {"uri": f"{EXT}/usage/v1", "required": False, "description": "토큰 사용량"},
        ],
    }
    if not BREAK_HINTS:
        caps["extensions"].insert(0, {
            "uri": f"{EXT}/tool-hints/v1", "required": False,
            "description": "스킬별 실행 힌트",
            "params": {"skills": {"catalog_search":
                                  {"readOnly": True, "destructive": False,
                                   "idempotent": True}}}})
    return {
        "name": "카탈로그 스텁",
        "description": "상품명으로 재고를 조회한다. 주문 생성과 배송 조회는 이 에이전트가 아니다.",
        "version": "1.0.0",
        "supportedInterfaces": [{"url": BASE, "protocolBinding": "HTTP+JSON",
                                 "protocolVersion": "1.0"}],
        "capabilities": caps,
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [{
            "id": "catalog_search", "name": "상품 검색",
            "description": "상품명으로 재고를 조회한다. 주문은 하지 않는다.",
            "tags": ["상품", "재고"],
            "examples": ["머그컵 재고 있어?", "A-1023 재고"],
        }],
    }


def agent_message(task, text):
    return {"messageId": mid(), "role": "ROLE_AGENT",
            "taskId": task["id"], "contextId": task["contextId"],
            "parts": [{"text": text}]}


def settle(task, state, text, artifacts=None):
    msg = agent_message(task, text)
    task["status"] = {"state": state, "timestamp": now(), "message": msg}
    task["history"].append(msg)          # §2.7 — 질문과 답을 둘 다 남긴다
    task["artifacts"] = artifacts or []
    task["metadata"] = {f"{EXT}/usage/v1": {"tokensIn": 120, "tokensOut": 30},
                        f"{EXT}/progress/v1": {"fraction": 1.0, "message": ""}}
    if state in TERMINAL:
        LOOP.pop(task["id"], None)       # 내부 상태는 태스크와 수명을 같이한다


def resolve(answer: str, candidates: list[dict]) -> dict | None:
    """재개 메시지를 **제시한 후보에 대해** 해석한다. 새 질의로 읽지 않는다(§2.7).

    이 함수가 이 스텁의 요점이다. 실제 에이전트라면 모델이 하겠지만, 무엇을 근거로
    해석해야 하는지는 같다 — 직전에 준 후보 목록이다.
    """
    text = answer.strip().lower()
    for i, c in enumerate(candidates):
        if c["sku"].lower() in text:                       # 식별자로 지목
            return c
    ordinals = [("첫", 0), ("1번", 0), ("두 번째", 1), ("2번", 1),
                ("세 번째", 2), ("3번", 2)]
    for word, idx in ordinals:                             # 순서로 지목
        if word in text and idx < len(candidates):
            return candidates[idx]
    for c in candidates:                                   # 표시의 일부로 지목
        for token in re.findall(r"[가-힣A-Za-z0-9]+", c["label"].lower()):
            if len(token) >= 2 and token in text:
                return c
    return None


def work(task_id: str, incoming_text: str, resuming: bool):
    """한 턴을 처리한다. 재개면 저장해 둔 상태를 근거로 해석한다."""
    time.sleep(0.3)
    with LOCK:
        task = TASKS.get(task_id)
        if task is None or task["status"]["state"] in TERMINAL:
            return
        state = LOOP.setdefault(task_id, {})

        if FAKE_RESUME:
            # 재개 상태를 안 들고 있는 구현. 답을 받아도 무엇에 대한 답인지 모르니
            # 처음부터 다시 묻는다 — 루프가 영원히 안 닫힌다.
            settle(task, "TASK_STATE_INPUT_REQUIRED",
                   "머그컵이 3종 있습니다. 어느 것입니까?",
                   [candidates_artifact(task, CATALOG)])
            return

        if resuming and state.get("candidates"):
            picked = resolve(incoming_text, state["candidates"])
            if picked is None:
                # 같은 질문을 그대로 반복하지 않는다(§2.7) — 무엇이 부족한지 밝힌다
                asked = state.get("asked_count", 1)
                state["asked_count"] = asked + 1
                labels = ", ".join(c["label"] for c in state["candidates"])
                settle(task, "TASK_STATE_INPUT_REQUIRED",
                       f"'{incoming_text}' 로는 특정하지 못했습니다. "
                       f"다음 중 하나를 상품 코드로 알려 주세요: {labels}",
                       [candidates_artifact(task, state["candidates"])])
                return
            # 원래 요청과 확정한 후보를 함께 써서 답을 만든다 — 원래 요청이 MUST 인 이유
            original = state.get("original", "")
            settle(task, "TASK_STATE_COMPLETED",
                   f"'{original}' 에 대해 {picked['label']} 재고는 {picked['stock']}개입니다.",
                   [{"artifactId": f"{task_id}:answer", "name": "answer",
                     "parts": [{"text": f"{picked['label']} 재고 {picked['stock']}개"}]},
                    {"artifactId": f"{task_id}:resolve", "name": "resolve",
                     "parts": [{"mediaType": "application/json", "data": picked}]}])
            return

        if CLARIFY:
            state["original"] = incoming_text        # 원래 요청 — MUST
            state["candidates"] = CATALOG            # 제시한 후보 — MUST
            state["asked_count"] = 1
            settle(task, "TASK_STATE_INPUT_REQUIRED",
                   "머그컵이 3종 있습니다. 어느 것입니까?",   # 직전 질문 — MUST
                   [candidates_artifact(task, CATALOG)])
            return

        item = CATALOG[0]
        settle(task, "TASK_STATE_COMPLETED",
               f"{item['label']} 재고 {item['stock']}개 있습니다.",
               [{"artifactId": f"{task_id}:answer", "name": "answer",
                 "parts": [{"text": f"{item['label']} 재고 {item['stock']}개 있습니다."}]}])


def candidates_artifact(task, items):
    return {"artifactId": f"{task['id']}:candidates", "name": "candidates",
            "parts": [{"mediaType": "application/json",
                       "data": [{"sku": c["sku"], "label": c["label"]} for c in items]}]}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/a2a+json"):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        # 소문자로 보낸다 — 실제 ASGI 서버들이 그렇게 한다. 헤더 조회가
        # 대소문자를 가리면 여기서 드러난다.
        self.send_header("content-type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, name, detail):
        self._send(code, {"type": f"https://a2a.dev/errors/{name}", "title": name,
                          "status": code, "detail": detail}, "application/problem+json")

    def _route(self):
        """접두사를 뗀 경로. 접두사 밖이면 None — API 가 거기 없다는 뜻이다."""
        p = self.path.split("?")[0]
        if not PREFIX:
            return p
        if p == PREFIX or p.startswith(PREFIX + "/"):
            return p[len(PREFIX):] or "/"
        return None

    def _version_ok(self) -> bool:
        """요청된 A2A 버전을 지원하는가(A2A 규격 3.6절). 카드는 협상 전이라 통과."""
        if self.path.split("?")[0] == "/.well-known/agent-card.json":
            return True
        raw = self.headers.get("A2A-Version") or ""
        if not raw and "A2A-Version=" in self.path:
            raw = self.path.split("A2A-Version=")[1].split("&")[0]
        version = ".".join(raw.strip().split(".")[:2]) if raw.strip() else "0.3"
        if version in SUPPORTED_VERSIONS:
            return True
        self._err(400, "VersionNotSupportedError",
                  f"지원하지 않는 A2A 버전입니다: {raw or '(없음)'}")
        return False

    def do_GET(self):
        if not self._version_ok():
            return
        if self.path.split("?")[0] == "/.well-known/agent-card.json":
            return self._send(200, card())
        route = self._route()
        if route is None:
            return self._err(501, "UnsupportedOperationError",
                             f"API 는 {PREFIX} 아래에 있습니다.")
        if m := re.fullmatch(r"/tasks/([^/?]+)", route):
            with LOCK:
                task = TASKS.get(m.group(1))
            if task is None:
                return self._err(404, "TaskNotFoundError",
                                 f"모르는 태스크입니다: {m.group(1)}")
            return self._send(200, task)
        if route == "/tasks":
            with LOCK:
                items = list(TASKS.values())
            return self._send(200, {"tasks": items, "nextPageToken": "",
                                    "pageSize": len(items), "totalSize": len(items)})
        self._err(404, "TaskNotFoundError", "없는 경로")

    def do_POST(self):
        if not self._version_ok():
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        route = self._route()
        if route is None:
            return self._err(501, "UnsupportedOperationError",
                             f"API 는 {PREFIX} 아래에 있습니다.")

        if route == "/message:send":
            return self._send_message(body)
        if route == "/message:stream":
            return self._err(400, "UnsupportedOperationError", "스트리밍을 지원하지 않습니다.")
        if m := re.fullmatch(r"/tasks/([^/]+):cancel", route):
            with LOCK:
                task = TASKS.get(m.group(1))
                if task is None:
                    return self._err(404, "TaskNotFoundError", "모르는 태스크")
                if task["status"]["state"] in TERMINAL:
                    return self._err(400, "TaskNotCancelableError", "이미 종결된 태스크입니다.")
                settle(task, "TASK_STATE_CANCELED", "취소되었습니다.")
                return self._send(200, task)
        self._err(404, "TaskNotFoundError", "없는 경로")

    def _send_message(self, body):
        msg = body.get("message") or {}
        cfg = body.get("configuration") or {}
        text = "\n".join(p.get("text", "") for p in msg.get("parts", []) if p.get("text"))
        task_id = msg.get("taskId")

        with LOCK:
            if task_id:
                task = TASKS.get(task_id)
                if task is None:
                    return self._err(404, "TaskNotFoundError",
                                     f"모르는 태스크입니다: {task_id}")
                if task["status"]["state"] in TERMINAL:
                    return self._err(400, "UnsupportedOperationError",
                                     "종결된 태스크에는 메시지를 보낼 수 없습니다.")
                ctx = msg.get("contextId")
                if ctx and ctx != task["contextId"]:
                    return self._err(400, "UnsupportedOperationError",
                                     "contextId 가 taskId 와 맞지 않습니다.")
                resuming = True
            else:
                task_id = "t_" + uuid.uuid4().hex[:10]
                task = {"id": task_id,
                        "contextId": msg.get("contextId") or "c_" + uuid.uuid4().hex[:10],
                        "status": {"state": "TASK_STATE_SUBMITTED", "timestamp": now()},
                        "artifacts": [], "history": []}
                TASKS[task_id] = task
                resuming = False

            task["history"].append(msg)       # 사용자 발화도 기록에 남긴다(§2.7)
            task["status"] = {"state": "TASK_STATE_WORKING", "timestamp": now()}
            snapshot = dict(task)

        if cfg.get("returnImmediately"):
            threading.Thread(target=work, args=(task_id, text, resuming), daemon=True).start()
            return self._send(200, {"task": snapshot})

        # 막고 기다린다 — 규격 A2A 규격 3.5절 는 둘 다 MUST 다
        work(task_id, text, resuming)
        with LOCK:
            return self._send(200, {"task": TASKS[task_id]})


server = HTTPServer(("127.0.0.1", PORT), H)
server.daemon_threads = True
server.serve_forever()
