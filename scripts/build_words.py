#!/usr/bin/env python3
"""data/lawdb_words.sqlite(낱말 색인)를 만든다 — law_search 의 SEARCH_METHOD=words·hybrid 가 쓴다.
lawdb.sqlite 를 만든 뒤에 돌린다. 형태소 분석기가 MCP 의존성이라 MCP 가상환경으로 돌린다(전체 약 17분, 8 프로세스).

  mcp/.venv/bin/python scripts/build_words.py [--db data/lawdb.sqlite] [--out data/lawdb_words.sqlite] [--procs 8]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp" / "src"))
from law_mcp.engine.words import build_words_index  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "lawdb.sqlite"))
    ap.add_argument("--out", default=str(ROOT / "data" / "lawdb_words.sqlite"))
    ap.add_argument("--procs", type=int, default=8)
    a = ap.parse_args()
    build_words_index(Path(a.db), Path(a.out), a.procs, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
