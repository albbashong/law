#!/usr/bin/env python3
"""conformance_check.py 자체의 회귀 테스트. 의존성 없음(unittest만).

두 가지를 검증한다.
  ① 검사기의 상수가 규격 정본(reference/)과 일치하는가 — 손으로 옮긴 값의 검증
  ② 검사기가 적합/부적합을 실제로 가려내는가 — stub_agent.py 로 확인

    python3 test_conformance_check.py
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REF = HERE / "reference"
CHECKER = HERE / "conformance_check.py"
STUB = REF / "stub_agent.py"

sys.path.insert(0, str(HERE))
import conformance_check as cc  # noqa: E402


class TestConstantsMatchSpec(unittest.TestCase):
    """검사기의 상수는 규격 문서에서 손으로 옮긴 것이다. 정본과 대조한다."""

    @classmethod
    def setUpClass(cls):
        cls.proto = (REF / "a2a.proto").read_text(encoding="utf-8")
        cls.spec = (REF / "specification.md").read_text(encoding="utf-8")

    def test_card_path(self):
        self.assertIn(cc.CARD_PATH, self.spec,
                      f"{cc.CARD_PATH} 가 규격 문서에 없다")

    def test_content_type(self):
        self.assertIn(cc.A2A_JSON, self.spec,
                      f"{cc.A2A_JSON} 이 규격 문서에 없다")

    def test_states_match_proto_enum(self):
        """TaskState enum 전체가 proto 와 정확히 일치해야 한다 — 빠져도 남아도 안 된다."""
        block = re.search(r"enum TaskState \{(.*?)\n\}", self.proto, re.S)
        self.assertIsNotNone(block, "proto 에서 enum TaskState 를 못 찾았다")
        from_proto = set(re.findall(r"(TASK_STATE_[A-Z_]+)\s*=", block.group(1)))
        self.assertEqual(cc.STATES, from_proto,
                         f"검사기에만: {cc.STATES - from_proto} / proto 에만: {from_proto - cc.STATES}")

    def test_terminal_states_are_marked_terminal_in_proto(self):
        """종결 상태 넷은 proto 주석이 'terminal state' 라고 밝힌 것과 같아야 한다."""
        block = re.search(r"enum TaskState \{(.*?)\n\}", self.proto, re.S).group(1)
        terminal = set()
        comment = ""
        for line in block.split("\n"):
            s = line.strip()
            if s.startswith("//"):
                comment += " " + s
                continue
            if m := re.match(r"(TASK_STATE_[A-Z_]+)\s*=", s):
                if "terminal state" in comment.lower():
                    terminal.add(m.group(1))
                comment = ""
        self.assertEqual(cc.TERMINAL, terminal,
                         f"검사기: {sorted(cc.TERMINAL)} / proto 주석: {sorted(terminal)}")

    def test_std_bindings_appear_in_spec(self):
        for b in cc.STD_BINDINGS:
            self.assertIn(f'"protocolBinding": "{b}"', self.spec,
                          f"바인딩 {b!r} 이 규격 예시에 없다")

    def test_required_card_fields_are_required_in_proto(self):
        """카드 필수 필드는 proto 에서 REQUIRED 로 표시된 것과 같아야 한다."""
        block = re.search(r"message AgentCard \{(.*?)\n\}", self.proto, re.S)
        self.assertIsNotNone(block)
        required = set()
        for line in block.group(1).split("\n"):
            if "REQUIRED" not in line:
                continue
            if m := re.search(r"(\w+)\s*=\s*\d+", line):
                # proto 는 snake_case, 카드 JSON 은 camelCase
                parts = m.group(1).split("_")
                required.add(parts[0] + "".join(p.title() for p in parts[1:]))
        self.assertEqual(set(cc.REQUIRED_CARD_FIELDS), required,
                         f"검사기: {sorted(cc.REQUIRED_CARD_FIELDS)} / proto: {sorted(required)}")

    def test_error_http_mapping(self):
        """오류→HTTP 매핑이 규격 §5.4 표와 일치해야 한다."""
        expected = {}
        for name, code in re.findall(r"\|\s*`(\w+Error)`\s*\|[^|]*\|[^|]*\|\s*`(\d{3})", self.spec):
            expected[name] = int(code)
        self.assertTrue(expected, "규격 문서에서 오류 매핑 표를 못 찾았다")
        for name, code in (("TaskNotFoundError", 404), ("InvalidAgentResponseError", 500)):
            self.assertEqual(expected.get(name), code,
                             f"{name} 은 규격상 {expected.get(name)} 인데 검사기는 {code} 로 다룬다")


class TestSnakeKeyDetection(unittest.TestCase):
    """2.3절 camelCase 검사는 규격이 정한 필드에만 적용된다."""

    def test_flags_protocol_field(self):
        self.assertIn("context_id", cc.snake_keys({"context_id": "c1"}))

    def test_ignores_free_form_areas(self):
        """params·metadata·data 아래는 자유 JSON 이다 — 스킬 id 가 snake_case 여도 적합하다."""
        for area in ("params", "metadata", "data"):
            with self.subTest(area=area):
                self.assertEqual([], cc.snake_keys({area: {"catalog_search": {"readOnly": True}}}))

    def test_ignores_extension_uri_keys(self):
        self.assertEqual([], cc.snake_keys({"https://x.org/ext/usage/v1": {"tokensIn": 1}}))

    def test_finds_nested(self):
        found = cc.snake_keys({"status": {"task_id": "t1"}})
        self.assertEqual(["status.task_id"], found)


def free_port() -> int:
    """비어 있는 포트를 커널에게 받는다.

    고정 포트를 쓰면 두 사람이 동시에 돌리거나 이전 실행의 스텁이 남아 있을 때
    바인딩에 실패해 테스트가 엉뚱하게 깨진다.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.2)
    return False


class StubCase(unittest.TestCase):
    """stub_agent.py 를 띄우고 검사기를 통째로 돌린다."""

    ARGS: list[str] = []

    @classmethod
    def setUpClass(cls):
        if cls is StubCase:
            raise unittest.SkipTest("기반 클래스")
        cls.PORT = free_port()
        cls.proc = subprocess.Popen([sys.executable, str(STUB), str(cls.PORT), *cls.ARGS],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait(f"http://localhost:{cls.PORT}/.well-known/agent-card.json"):
            cls.proc.kill()
            raise unittest.SkipTest(f"스텁이 {cls.PORT} 에 안 떴다")

    @classmethod
    def tearDownClass(cls):
        proc = getattr(cls, "proc", None)
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=5)

    def run_checker(self, *extra):
        r = subprocess.run([sys.executable, str(CHECKER), f"http://localhost:{self.PORT}", *extra],
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout


class TestConformantStub(StubCase):

    def test_error_content_type_is_recognised(self):
        """헤더 조회가 대소문자를 가리면 실서버에서 조용히 경고가 뜬다."""
        _, out = self.run_checker()
        assert "오류가 problem+json 으로 온다" in out, out

    def test_version_negotiation_is_checked(self):
        """A2A 규격 3.6절 — 모르는 버전을 거절해야 합니다."""
        _, out = self.run_checker()
        assert "모르는 A2A-Version" in out, out

    def test_passes_without_probe(self):
        code, out = self.run_checker()
        self.assertEqual(0, code, out)

    def test_passes_with_probe(self):
        code, out = self.run_checker("--probe", "재고 있어?")
        self.assertEqual(0, code, out)
        self.assertIn("answer", out)

    def test_reports_state_transition_in_order(self):
        """궤적은 관찰 순서대로여야 하고 종결로 끝나야 한다.

        시작 상태가 SUBMITTED 인지 WORKING 인지는 구현 자유다 — 규격은 그걸
        정하지 않는다. 여기서 잡으려는 것은 순서가 뒤섞이는 버그다.
        """
        _, out = self.run_checker("--probe", "재고 있어?")
        line = next(l for l in out.splitlines() if "상태 전이:" in l)
        trail = [s.strip() for s in line.split("상태 전이:")[1].split("→")]
        assert trail[-1] == "COMPLETED", line
        assert "COMPLETED" not in trail[:-1], f"종결이 중간에 나왔다: {line}"
        assert len(trail) == len(set(trail)), f"같은 상태가 반복됐다: {line}"


class TestPlaceholderExtensionDomain(StubCase):
    """확장 URI 를 안 정하고 예시대로 두면 알려야 합니다.

    안 정하면 상대가 확장을 못 읽고 **오류도 안 납니다** — 그냥 무시됩니다.
    다만 프로파일이라 A2A 적합성에는 영향이 없습니다.
    """

    def test_flagged_but_still_a2a_conformant(self):
        code, out = self.run_checker()
        self.assertEqual(0, code, out)
        self.assertIn("자리표시자", out)
        self.assertIn("A2A 적합하다", out)


class TestMissingHints(StubCase):
    """tool-hints 가 없으면 **알려야** 한다 — 조용히 병렬이 죽는 결함이다.

    다만 이것은 이 플랫폼의 프로파일이지 A2A 요건이 아니다. 그래서 **불합격이
    아니다** — 표준을 지킨 남의 에이전트를 프로파일로 떨어뜨리면 안 된다.
    """

    ARGS = ["--break-hints"]

    def test_reported_but_still_a2a_conformant(self):
        code, out = self.run_checker()
        self.assertEqual(0, code, out)
        self.assertIn("4.1.1절", out)
        self.assertIn("A2A 적합하다", out)

    def test_flagged_under_the_profile_layer(self):
        _, out = self.run_checker()
        assert "프로파일" in out and "A2A 적합성과 무관하다" in out, out


class TestClarifyStub(StubCase):
    """되묻기로 끝나는 경로도 적합이다."""

    ARGS = ["--clarify"]

    def test_passes_and_checks_candidates(self):
        code, out = self.run_checker("--probe", "주문해줘")
        self.assertEqual(0, code, out)
        self.assertIn("되묻기가 형식을 지킨다", out)
        self.assertIn("구별된다", out)


class TestPathPrefix(StubCase):
    """API 가 경로 접두사 아래 있으면 검사기는 **카드를 따라가야** 한다.

    well-known 은 origin 당 하나라 origin 에서 읽지만(규격 §8.2), 엔드포인트는
    supportedInterfaces[].url 이 가리키는 곳에 있다(규격 2.4.1절). origin 에만
    붙는 검사기는 여기서 전부 실패한다.
    """

    ARGS = ["--prefix", "/agents/catalog"]

    def test_follows_card_url(self):
        code, out = self.run_checker("--probe", "재고 있어?")
        self.assertEqual(0, code, out)
        self.assertIn("/agents/catalog", out)

    def test_reaches_endpoints_under_prefix(self):
        _, out = self.run_checker("--probe", "재고 있어?")
        self.assertIn("→ COMPLETED", out)
        self.assertIn("종결 태스크에 온 메시지를 400 으로 거절한다", out)


class TestClarifyLoopCloses(StubCase):
    """되묻기에 답하면 루프가 닫혀야 한다 — 형식이 아니라 왕복을 본다."""

    ARGS = ["--clarify"]

    def test_resume_continues_same_task(self):
        code, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertEqual(0, code, out)
        self.assertIn("재개가 같은 태스크를 이어간다", out)

    def test_loop_actually_closes(self):
        _, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertIn("되묻기 루프가 닫혔다", out)

    def test_history_records_the_exchange(self):
        _, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertIn("Task.history 에 왕복이 남았다", out)

    def test_blocking_mode_waits(self):
        """규격 A2A 규격 3.5절 — returnImmediately 없으면 종결·중단까지 막고 기다린다."""
        _, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertIn("블로킹 모드가 끝난 상태로 돌아왔다", out)


class TestBrokenResumeLoop(StubCase):
    """재개 상태를 안 들고 있으면 잡아야 한다.

    실측에서 통과율 0% 를 만든 실패 모드다. 얕은 검사(형식·후보 구별·같은 태스크)는
    전부 통과하므로, 왕복을 실제로 돌지 않으면 이 결함이 그대로 배포된다.
    """

    ARGS = ["--fake-resume"]

    def test_fails(self):
        code, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertEqual(1, code, out)
        self.assertIn("똑같은 질문이 돌아왔다", out)

    def test_shallow_checks_still_pass(self):
        """왜 왕복 검사가 필요한지 — 형식만 보면 이 결함이 안 잡힌다."""
        _, out = self.run_checker("--probe", "머그컵 재고 있어?")
        self.assertIn("되묻기가 형식을 지킨다", out)
        self.assertIn("후보 3개가 서로 구별된다", out)
        self.assertIn("재개가 같은 태스크를 이어간다", out)


class TestUnreachable(unittest.TestCase):
    def test_exit_code_2(self):
        r = subprocess.run([sys.executable, str(CHECKER), "http://localhost:1"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(2, r.returncode, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
