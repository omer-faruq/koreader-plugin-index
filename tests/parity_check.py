"""Fail the build when the page's ranking drifts from the reference ranking.

The search page reimplements scripts/rank.py in JavaScript because the page has
to rank without a Python runtime. Two implementations of the same rules will
drift, and the failure is silent: tests/queries.toml keeps passing against the
Python side while users see something else. This compares them directly.
"""

import json
import pathlib
import subprocess
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import rank  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    index_path = ROOT / "docs" / "index.json"
    if not index_path.exists():
        print("no index -- run build.py first", file=sys.stderr)
        return 1

    try:
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "parity.mjs")],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        # Node is not a hard requirement for building the index, only for
        # checking the page against it.
        print("node not available, skipping parity check")
        return 0

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1

    js = json.loads(result.stdout)
    entries = json.loads(index_path.read_text(encoding="utf-8"))["plugins"]
    cases = tomllib.loads((ROOT / "tests" / "queries.toml").read_text(encoding="utf-8"))["case"]

    diverged = []
    for case in cases:
        query = case["query"]
        py = [e["id"] for e in rank.rank(entries, query, limit=3)]
        if py != js.get(query):
            diverged.append((query, py, js.get(query)))

    for query, py, got in diverged:
        print(f"  DRIFT  {query}")
        print(f"         rank.py:  {py}")
        print(f"         page:     {got}")

    if diverged:
        print(f"\n{len(diverged)}/{len(cases)} queries rank differently in the page "
              f"than in rank.py", file=sys.stderr)
        return 1

    print(f"ranking parity ok across {len(cases)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
