"""새로 만든 색인 검사 — 문제가 있으면 이유를 적고 1 로 끝난다(update.sh 가 바꿔 끼우기 전에 부른다).
  python scripts/check_index.py --new data/lawdb.sqlite.new [--old data/lawdb.sqlite] [--min-ratio 0.9]"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from indexer.check import counts, problems  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", default=None)
    ap.add_argument("--min-ratio", type=float, default=0.9)
    a = ap.parse_args()
    for k, v in counts(Path(a.new)).items():
        print(f"{k}: {v}")
    found = problems(Path(a.new), Path(a.old) if a.old else None, a.min_ratio)
    for p in found:
        print(f"문제 — {p}", file=sys.stderr)
    sys.exit(1 if found else 0)
