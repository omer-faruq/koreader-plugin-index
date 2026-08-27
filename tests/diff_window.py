"""What a diff run asks GitHub for, when the night before did not go to plan.

A diff is only as good as its window. The window used to be a fixed two days
back from today, which covers a late run but not a missing one -- and missing
ones happen: GitHub's scheduler dropped runs on 26 August 2026 and started the
next day's eleven hours late. A night that never runs puts every repository
pushed inside it outside the window of every run afterwards, and nothing
anywhere reports that. The index just goes stale in places.

So the window now also reaches back to the published index's own generated_at.
That is arithmetic over dates with an off-by-one in it (`pushed:>DATE` excludes
DATE) and a fallback behind it, decided once a day in a run nobody watches.
This drives it directly, offline, against a fixed clock.
"""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402

NOW = datetime.datetime(2026, 8, 27, 6, 0, tzinfo=datetime.timezone.utc)
FLOOR, LIMIT = 2, 14


def window(stamp):
    previous = {"generated_at": stamp} if stamp is not None else None
    return build.diff_window(previous, FLOOR, LIMIT, now=NOW)


def covers(since, day):
    """Does `pushed:>since` return a repository pushed on `day`?

    Strictly greater, and day-granular: a repository pushed at any hour of
    `since` itself is not returned. That asymmetry is the whole reason the
    window subtracts a day from the last build.
    """
    return datetime.date.fromisoformat(since) < datetime.date.fromisoformat(day)


def an_ordinary_day_is_unchanged():
    since, _ = window("2026-08-26T04:08:07Z")
    assert since == "2026-08-25", since


def a_skipped_night_is_covered():
    # Last built on the 25th, nothing ran on the 26th. A repository pushed
    # that day is the one the old fixed window would have lost for good.
    since, why = window("2026-08-25T04:05:38Z")
    assert covers(since, "2026-08-26"), f"{since} misses the skipped day"
    assert "2026-08-25" in why, why


def the_last_build_s_own_day_is_covered():
    # The build ran at 04:00; pushes at 23:00 that same day came after it and
    # were never seen. The window has to include the day it ran, not start
    # after it -- `pushed:>DATE` excludes DATE.
    #
    # Dated far enough back that generated_at is what decides the window. On
    # an ordinary day the two-day floor sits behind the last build anyway and
    # would cover this whether the arithmetic were right or not.
    since, _ = window("2026-08-20T04:00:00Z")
    assert since == "2026-08-19", since
    assert covers(since, "2026-08-20"), f"{since} misses the last build's own day"


def a_long_gap_is_rebuilt_in_full():
    since, why = window("2026-08-07T04:00:00Z")
    assert since is None, since
    assert "14-day" in why, why


def the_gap_limit_is_a_ceiling_not_a_cliff():
    # Just inside the limit still diffs. If this ever flips, a fortnight of
    # outage turns into a full rebuild one day early -- correct, but it means
    # the boundary moved without anyone saying so.
    since, _ = window("2026-08-14T04:00:00Z")
    assert since == "2026-08-13", since


def the_window_only_ever_widens():
    # A stamp from the future -- clock skew, a hand-run build, a republish --
    # must not pull the window in and hand back a narrower diff than the floor
    # promises. generated_at is allowed to reach further back, nothing else.
    since, _ = window("2026-08-28T04:00:00Z")
    assert since == "2026-08-25", since


def an_unusable_stamp_keeps_the_floor():
    for stamp in ("not-a-date", "", None):
        since, _ = window(stamp)
        assert since == "2026-08-25", f"{stamp!r} gave {since}"


def a_missing_stamp_keeps_the_floor():
    since, _ = build.diff_window({}, FLOOR, LIMIT, now=NOW)
    assert since == "2026-08-25", since


CASES = [
    ("an ordinary day is unchanged", an_ordinary_day_is_unchanged),
    ("a skipped night is covered", a_skipped_night_is_covered),
    ("the last build's own day is covered", the_last_build_s_own_day_is_covered),
    ("a long gap is rebuilt in full", a_long_gap_is_rebuilt_in_full),
    ("the gap limit is a ceiling, not a cliff", the_gap_limit_is_a_ceiling_not_a_cliff),
    ("the window only ever widens", the_window_only_ever_widens),
    ("an unusable stamp keeps the floor", an_unusable_stamp_keeps_the_floor),
    ("a missing stamp keeps the floor", a_missing_stamp_keeps_the_floor),
]


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
        print(f"\n{len(failures)}/{len(CASES)} diff window checks failed", file=sys.stderr)
        return 1
    print(f"\ndiff window ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
