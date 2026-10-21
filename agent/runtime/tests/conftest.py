"""런타임 테스트 공용 장치 — `run_query` 가 읽는 prompt.txt 를 임시 파일로 준다.

AGENT_DIR 이 없는 테스트 환경에서 `loop.PROMPT_PATH` 는 cwd 의 prompt.txt 를 가리킨다.
리포에 자리표시자 prompt.txt 를 두는 대신 테스트마다 임시 파일을 물린다.
"""

import pytest


@pytest.fixture(autouse=True)
def prompt_file(monkeypatch, tmp_path):
    import loop

    path = tmp_path / "prompt.txt"
    path.write_text("테스트 프롬프트", encoding="utf-8")
    monkeypatch.setattr(loop, "PROMPT_PATH", path)
    return path
