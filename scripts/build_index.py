#!/usr/bin/env python3
"""data/lawdb.sqlite 를 만든다. 보고서는 data/build_report.json.

  .venv/bin/python scripts/build_index.py [--out data/lawdb.sqlite]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indexer.build import DATA, build  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA / "lawdb.sqlite"))
    args = ap.parse_args()
    report = build(Path(args.out), log=lambda m: print(m, flush=True))
    (DATA / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    short = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}
    print(json.dumps(short, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
