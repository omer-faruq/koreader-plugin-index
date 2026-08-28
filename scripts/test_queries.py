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


def breakdown(entry, tokens, raw_words=()):
    """Per-field hit counts, so a loss can be diagnosed rather than guessed at."""
    fields = {
        "kw": (rank.WEIGHT_KEYWORD, " ".join(entry.get("keywords", [])), False),
        "purp": (rank.WEIGHT_PURPOSE, entry.get("purpose", ""), False),
        "desc": (rank.WEIGHT_DESCRIPTION, entry.get("description", ""), False),
        "cat": (rank.WEIGHT_CATEGORY, " ".join(entry.get("categories", [])), False),
        "name": (rank.WEIGHT_NAME, " ".join(rank.name_words(rank.name_of(entry))), True),
    }
    parts = []
    for label, (weight, text, substring) in fields.items():
        n = rank._hits(tokens, text, substring)
        if n:
            parts.append(f"{label}×{n}={weight * n:.1f}")
    # Named even when it is absent: "the query was this plugin's own name and
    # it still lost" and "the name never matched" are different diagnoses, and
    # a line that only appears on a win cannot tell them apart.
    if rank.title_match(entry, tokens, raw_words):
        parts.append(f"title=+{rank.TITLE_BONUS:.1f}")
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
    raw = rank.query_words(query)
    by_id = {e["id"]: e for e in entries}
    print(f"          tokens: {tokens}")
    for label, entry_id in [("want", expected[0]), ("won ", top[0] if top else None)]:
        entry = by_id.get(entry_id) if entry_id else None
        if not entry:
            print(f"          {label} {entry_id}: NOT IN INDEX")
            continue
        total = rank.score(entry, tokens, raw)
        print(f"          {label} {entry_id}  = {total:.1f}   {breakdown(entry, tokens, raw)}")
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

# Every case names a third-party repository, and any of them can be deleted,
# renamed or transferred overnight by someone with no idea this suite exists.
# That is not a ranking regression, and failing on it stops the nightly build
# from publishing anything at all -- an unrelated stranger's `git push` taking
# the site down until a human edits a TOML file. This build runs unattended by
# design, so it has to survive the ecosystem moving under it.
#
# Three steps, in order of how much is still known.
#
# The owner was always incidental. A case asks "does a reading-streak tracker
# win for this query", and `advokatb/readingstreak.koplugin` is how that was
# spelled on the day it was written. When that exact id is gone, any plugin of
# the same name answers the same question, and the catalogue is full of forks
# that outlive their originals. So a case repoints itself by name first.
#
# Only when nothing of that name is left anywhere is the case genuinely about
# something the ecosystem no longer has. It is reported and skipped, not failed.
#
# With a ceiling, because the same symptom has a second cause: if extraction
# breaks or the index is truncated, *everything* goes missing at once, and a
# suite that quietly skipped its way to green would be the worst possible
# outcome. Churn takes out one case at a time; a broken build takes out most.
MAX_RETIRED_SHARE = 0.25


def slug_of(name):
    """A plugin's name with the owner, the suffix and the punctuation gone."""
    return "".join(rank.name_words(name.split("/", 1)[-1]))


def resolve(expected, entries, by_id):
    """Which entries a case is really about, now.

    Returns the ids to judge against and how they were arrived at: `None` when
    the case still names something in the index, or the substitutes found by
    name when it does not.
    """
    live = [want for want in expected if want in by_id]
    if live:
        return live, None
    # Short names are not distinctive enough to repoint on -- `sync` would
    # match half the catalogue -- and the same floor is used for deciding a
    # name is a name at all.
    wanted = {slug_of(want) for want in expected}
    wanted = {slug for slug in wanted if len(slug) >= rank.MIN_TITLE_SLUG}
    if not wanted:
        return [], None
    moved = [e["id"] for e in entries if slug_of(rank.name_of(e)) in wanted]
    return moved, (moved or None)


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
    # The failure explanation prints stars and plugin names, and a Windows
    # console defaults to cp1252 -- so the one path that says why a case failed
    # died with a UnicodeEncodeError instead of saying anything, on the machine
    # most likely to be reading it. The runner is UTF-8 and never saw this.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

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

    by_id = {e["id"]: e for e in entries}
    failures, retired, moved = [], [], []
    for case in cases:
        query = case["query"]
        expected = case["expect_top3"]
        # Judged only against the answers that still exist. A case listing two
        # right answers where one has been deleted is still a live case.
        live, substitutes = resolve(expected, entries, by_id)
        if substitutes:
            moved.append((query, expected, substitutes))
        if not live:
            retired.append(query)
            print(f"  gone  {query}")
            print(f"          not in the index any more: {', '.join(expected)}")
            print(f"          got:                       "
                  f"{', '.join(e['id'] for e in rank.rank(entries, query, limit=TOP_N)) or '(nothing)'}")
            continue
        top = [e["id"] for e in rank.rank(entries, query, limit=TOP_N)]
        # Any one of the expected answers in the top three is a pass: several
        # cases legitimately have more than one right answer, and the point is
        # that the user is shown something that solves their problem.
        hit = any(want in top for want in live)
        print(f"  {'ok  ' if hit else 'FAIL'}  {query}")
        if not hit:
            print(f"          expected one of: {', '.join(live)}")
            print(f"          got:             {', '.join(top) or '(nothing)'}")
            explain_failure(entries, query, live, top)
            failures.append(query)

    print()
    if moved:
        print("Repointed by name -- the id in the file is gone, the plugin is not:")
        for query, expected, substitutes in moved:
            print(f"  {query}: {', '.join(expected)} -> {', '.join(substitutes)}")
        print()
    if retired:
        print(f"{len(retired)} case(s) name a plugin the index no longer has. "
              f"Retire or repoint them in {CASES.name}:")
        for query in retired:
            print(f"  {query}")
        print()
    too_many = len(retired) > MAX_RETIRED_SHARE * len(cases)
    if failures or coverage_problems or too_many:
        if failures:
            print(f"{len(failures)}/{len(cases)} failed", file=sys.stderr)
        if coverage_problems:
            print(f"{len(coverage_problems)} coverage floor(s) breached", file=sys.stderr)
        if too_many:
            print(f"{len(retired)}/{len(cases)} cases have no answer left in the index, "
                  f"over the {MAX_RETIRED_SHARE:.0%} ceiling -- that is an index "
                  f"problem, not repositories going away", file=sys.stderr)
        return 1
    judged = len(cases) - len(retired)
    print(f"all {judged} passed" + (f" ({len(retired)} skipped)" if retired else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
