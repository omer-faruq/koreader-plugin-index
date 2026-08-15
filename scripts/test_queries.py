"""Run the ranking quality suite against the freshly built index.

Exits non-zero when a known answer drops out of the top three, so ranking
quality degrading over time shows up as a failed build rather than as silently
worse recommendations.
"""

import json
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import rank  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "index.json"
CASES = ROOT / "tests" / "queries.toml"

TOP_N = 3


def main():
    if not INDEX.exists():
        print(f"no index at {INDEX} -- run build.py first", file=sys.stderr)
        return 1

    with INDEX.open(encoding="utf-8") as handle:
        entries = json.load(handle).get("plugins", [])
    with CASES.open("rb") as handle:
        cases = tomllib.load(handle).get("case", [])

    print(f"{len(cases)} queries against {len(entries)} plugins\n")

    failures = []
    for case in cases:
        query = case["query"]
        expected = case["expect_top3"]
        top = [e["id"] for e in rank.rank(entries, query, limit=TOP_N)]
        # Any one of the expected answers in the top three is a pass: several
        # cases legitimately have more than one right answer, and the point is
        # that the user is shown something that solves their problem.
        hit = any(want in top for want in expected)
        print(f"  {'ok  ' if hit else 'FAIL'}  {query}")
        if not hit:
            print(f"          expected one of: {', '.join(expected)}")
            print(f"          got:             {', '.join(top) or '(nothing)'}")
            failures.append(query)

    print()
    if failures:
        print(f"{len(failures)}/{len(cases)} failed", file=sys.stderr)
        return 1
    print(f"all {len(cases)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
