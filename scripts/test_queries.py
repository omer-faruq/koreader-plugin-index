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


def breakdown(entry, tokens):
    """Per-field hit counts, so a loss can be diagnosed rather than guessed at."""
    fields = {
        "kw": (rank.WEIGHT_KEYWORD, " ".join(entry.get("keywords", []))),
        "purp": (rank.WEIGHT_PURPOSE, entry.get("purpose", "")),
        "desc": (rank.WEIGHT_DESCRIPTION, entry.get("description", "")),
        "cat": (rank.WEIGHT_CATEGORY, " ".join(entry.get("categories", []))),
        "name": (rank.WEIGHT_NAME, entry.get("repo", "")),
    }
    parts = []
    for label, (weight, text) in fields.items():
        n = rank._hits(tokens, text)
        if n:
            parts.append(f"{label}×{n}={weight * n:.1f}")
    tier = entry.get("tier", "?")
    parts.append(f"tier{tier}={rank.TIER_BONUS.get(tier, 0):+.1f}")
    parts.append(f"★{entry.get('stars', 0)}")
    return " ".join(parts)


def explain_failure(entries, query, expected, top):
    """Show why the expected answer lost to whatever won.

    Tuning ranking against a bare pass/fail is guesswork; the interesting
    number is always which field the expected entry failed to match on.
    """
    tokens = rank.tokenise(query)
    by_id = {e["id"]: e for e in entries}
    print(f"          tokens: {tokens}")
    for label, entry_id in [("want", expected[0]), ("won ", top[0] if top else None)]:
        entry = by_id.get(entry_id) if entry_id else None
        if not entry:
            print(f"          {label} {entry_id}: NOT IN INDEX")
            continue
        total = rank.score(entry, tokens)
        print(f"          {label} {entry_id}  = {total:.1f}   {breakdown(entry, tokens)}")
        if label == "want":
            print(f"               keywords: {(entry.get('keywords') or [])[:12]}")
            print(f"               purpose:  {(entry.get('purpose') or '')[:110]}")


# Floors, not targets. Measured on the August 2026 catalogue: 92.6% of plugins
# were reachable in English and 2.4% had no prose at all. These sit far enough
# below that to survive the catalogue growing a Chinese-documented tail, and
# close enough to catch the failure they exist for -- extraction silently
# returning nothing, or the English view of a non-English README breaking and
# taking a whole language back out of the index. A ratio rather than a count,
# because the catalogue grows and a count would need editing every few months.
MIN_ENGLISH_SHARE = 0.85
MAX_SILENT_SHARE = 0.05


def check_coverage(index):
    """Refuse a run that can no longer be searched, whatever it ranks.

    The query cases above measure whether the right answer wins. They cannot
    see an entry that dropped out of contention entirely: a plugin scoring zero
    against every query never appears in any case, so the suite stays green
    while the catalogue quietly shrinks underneath it.
    """
    coverage = index.get("coverage")
    if not coverage:
        # An index built before coverage existed. Not a failure -- there is
        # simply nothing to check, and saying so beats a confusing pass.
        print("  no coverage block in the index; skipping")
        return []
    total = coverage.get("plugins") or 1
    english = coverage["english"] / total
    silent = coverage["silent"] / total
    problems = []
    print(f"  reachable in English {english:.1%} (floor {MIN_ENGLISH_SHARE:.0%})")
    print(f"  no prose at all      {silent:.1%} (ceiling {MAX_SILENT_SHARE:.0%})")
    if english < MIN_ENGLISH_SHARE:
        problems.append(
            f"only {english:.1%} of plugins are reachable in English, "
            f"below the {MIN_ENGLISH_SHARE:.0%} floor")
    if silent > MAX_SILENT_SHARE:
        problems.append(
            f"{silent:.1%} of plugins have no prose at all, "
            f"above the {MAX_SILENT_SHARE:.0%} ceiling")
    return problems


def main():
    if not INDEX.exists():
        print(f"no index at {INDEX} -- run build.py first", file=sys.stderr)
        return 1

    with INDEX.open(encoding="utf-8") as handle:
        index = json.load(handle)
    entries = index.get("plugins", [])
    with CASES.open("rb") as handle:
        cases = tomllib.load(handle).get("case", [])

    print("coverage")
    coverage_problems = check_coverage(index)
    for problem in coverage_problems:
        print(f"  FAIL  {problem}", file=sys.stderr)
    print()

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
            explain_failure(entries, query, expected, top)
            failures.append(query)

    print()
    if failures or coverage_problems:
        if failures:
            print(f"{len(failures)}/{len(cases)} failed", file=sys.stderr)
        if coverage_problems:
            print(f"{len(coverage_problems)} coverage floor(s) breached", file=sys.stderr)
        return 1
    print(f"all {len(cases)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
