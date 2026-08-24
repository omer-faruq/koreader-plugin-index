"""Prove the coverage gate fires.

A gate nobody has watched trip is indistinguishable from no gate at all, and
this one exists precisely to catch a failure that is invisible from the
outside: entries the scorer cannot read score zero against every query, so the
ranking suite stays green while the searchable catalogue shrinks underneath it.
If that gate were itself broken, nothing else here would notice.

Runs offline against synthetic indexes -- the point is the threshold logic, not
the catalogue.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402
import extract  # noqa: E402
import test_queries  # noqa: E402


def plugin(purpose="", description="", keywords=("a", "b", "c")):
    return {"purpose": purpose, "description": description, "keywords": list(keywords)}


def index_of(plugins):
    return {"coverage": build.coverage_of(plugins)}


CHINESE = "在 KOReader 上阅读知乎日报，通过公开接口抓取内容并在本地构建 EPUB 打开。"
ENGLISH = "Read the daily digest inside KOReader by building an EPUB locally."

CASES = []


def case(name, fn):
    CASES.append((name, fn))


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)


def readability_classifies():
    expect(extract.readability(ENGLISH) == "english", "English prose misread")
    expect(extract.readability(CHINESE) == "unreadable", "Chinese prose misread")
    expect(extract.readability("") == "silent", "empty purpose misread")
    expect(extract.readability("", ENGLISH) == "english",
           "description should stand in for a missing purpose")
    expect(extract.readability("   ") == "silent", "whitespace is not prose")


def counts_add_up():
    plugins = [plugin(ENGLISH)] * 9 + [plugin(CHINESE)] + [plugin()]
    coverage = build.coverage_of(plugins)
    expect(coverage["plugins"] == 11, coverage)
    expect(coverage["english"] == 9, coverage)
    expect(coverage["unreadable"] == 1, coverage)
    expect(coverage["silent"] == 1, coverage)
    expect(coverage["english"] + coverage["unreadable"] + coverage["silent"]
           == coverage["plugins"], "every plugin must land in exactly one bucket")


def thin_keywords_counted():
    plugins = [plugin(ENGLISH, keywords=("one", "two")), plugin(ENGLISH)]
    expect(build.coverage_of(plugins)["thin_keywords"] == 1, "thin keyword count wrong")


def healthy_index_passes():
    plugins = [plugin(ENGLISH) for _ in range(95)] + [plugin(CHINESE) for _ in range(5)]
    expect(test_queries.check_coverage(index_of(plugins)) == [],
           "a healthy catalogue must not trip the gate")


def a_language_dropping_out_fails():
    """The regression this whole gate exists for."""
    plugins = [plugin(ENGLISH) for _ in range(80)] + [plugin(CHINESE) for _ in range(20)]
    problems = test_queries.check_coverage(index_of(plugins))
    expect(len(problems) == 1 and "reachable in English" in problems[0],
           f"expected the English floor to trip, got {problems}")


def extraction_returning_nothing_fails():
    plugins = [plugin(ENGLISH) for _ in range(90)] + [plugin() for _ in range(10)]
    problems = test_queries.check_coverage(index_of(plugins))
    expect(any("no prose at all" in p for p in problems),
           f"expected the silent ceiling to trip, got {problems}")


def old_index_skips_rather_than_failing():
    expect(test_queries.check_coverage({"plugins": []}) == [],
           "an index built before coverage existed must not fail the build")


for name, fn in [
    ("readability classifies the three states", readability_classifies),
    ("coverage counts add up", counts_add_up),
    ("thin keyword lists are counted", thin_keywords_counted),
    ("a healthy catalogue passes", healthy_index_passes),
    ("a language dropping out fails the build", a_language_dropping_out_fails),
    ("extraction returning nothing fails the build", extraction_returning_nothing_fails),
    ("an index without coverage is skipped", old_index_skips_rather_than_failing),
]:
    case(name, fn)


def main():
    failures = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            print(f"  FAIL  {name}\n          {exc}", file=sys.stderr)
            failures.append(name)
        else:
            print(f"  ok    {name}")
    if failures:
        print(f"\n{len(failures)}/{len(CASES)} coverage gate checks failed", file=sys.stderr)
        return 1
    print(f"\ncoverage gate ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
