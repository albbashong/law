#!/usr/bin/env python3
"""data/lawdb_vec/(법령 조문 임베딩 색인)를 만든다 — law_search 의 SEARCH_METHOD 에 vector 가 들면 쓴다.
lawdb.sqlite 를 만들거나 갱신한 뒤에 돌린다. 글이 바뀌지 않은 조문은 앞 색인의 벡터를 그대로 쓰므로 갱신 뒤에는 개정분만
임베딩한다(처음 만들 때는 조문 약 20만 개, 로컬 ollama qwen3-embedding:0.6b 로 약 90분). 끊기면 다시 돌려 이어서 만든다.
임베딩할 곳은 MCP 설정과 같다(EMBED_URL·EMBED_MODEL).

  mcp/.venv/bin/python scripts/build_vectors.py [--db data/lawdb.sqlite] [--out data/lawdb_vec] [--batch 128]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp" / "src"))
from law_mcp.engine.vectors import build_vector_index  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "lawdb.sqlite"))
    ap.add_argument("--out", default=str(ROOT / "data" / "lawdb_vec"))
    ap.add_argument("--batch", type=int, default=128)
    a = ap.parse_args()
    build_vector_index(Path(a.db), Path(a.out), a.batch, log=lambda m: print(m, flush=True))


if __name__ == "__main__":
    main()
