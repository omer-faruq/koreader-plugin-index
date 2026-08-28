"""How long a failed request waits, and by what reasoning.

Retry behaviour is the least observable code in the build: it runs at 03:23,
against someone else's outage, and both of its failure modes are silent. Wait
too little on a rate limit and the run dies; wait too much on a transient error
and a six-minute build takes eleven, which is what happened before the ladders
were split. Neither shows up as a failure unless a test says so.

Offline: the transport is replaced and nothing sleeps. What is checked is the
decision -- which ladder, how long, and when to stop.
"""

import email.message
import pathlib
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import github  # noqa: E402

# What _post returns: the envelope, undisturbed. Unwrapping `data` is graphql's
# job, one layer up, and pinning that here would test the wrong function.
OK = {"data": {"ok": True}}


def headers(**fields):
    msg = email.message.Message()
    for key, value in fields.items():
        msg[key.replace("_", "-")] = str(value)
    return msg


def http(code, **fields):
    return urllib.error.HTTPError("https://x", code, "err", headers(**fields), None)


def run(*failures):
    """Drive one _post through a scripted sequence of failures.

    Returns the waits it chose, and either the decoded body or the exception it
    gave up with.
    """
    waits = []
    remaining = list(failures)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"data": {"ok": true}}'

    def urlopen(req, timeout=None):
        if remaining:
            raise remaining.pop(0)
        return Response()

    client = github.Client(token="t", verbose=False)
    old_open, old_sleep = github.urllib.request.urlopen, github.time.sleep
    github.urllib.request.urlopen = urlopen
    github.time.sleep = waits.append
    try:
        return waits, client._post("https://x", {})
    except Exception as exc:  # noqa: BLE001 -- the outcome under test
        return waits, exc
    finally:
        github.urllib.request.urlopen = old_open
        github.time.sleep = old_sleep


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)


def a_transient_error_is_answered_in_seconds():
    """The whole point. A 502 clears on its own, and four or five a night is
    the difference between a six-minute build and an eleven-minute one."""
    waits, out = run(http(502))
    expect(waits == [2], f"a 502 should cost seconds, waited {waits}")
    expect(out == OK, f"the retry should succeed, got {out}")


def a_rate_limit_is_answered_in_minutes():
    """The opposite case, and why the ladders cannot be merged again: against
    the limiter, waiting is the only thing that helps."""
    waits, _ = run(http(403))
    expect(waits == [60], f"a 403 without headers waits a minute, got {waits}")


def the_limiter_is_believed_when_it_says_how_long():
    """Guessing is what the ladder is for. When the answer is in the response,
    using it beats a fixed minute in both directions."""
    waits, _ = run(http(429, Retry_After=7))
    expect(waits == [7], f"Retry-After should be honoured, got {waits}")


def a_reset_time_counts_only_once_the_budget_is_gone():
    """`x-ratelimit-reset` rides on every response, including ones that had
    quota left. Reading it unconditionally would sleep to the top of the hour
    over an unrelated 403."""
    now = int(github.time.time())
    waits, _ = run(http(403, X_RateLimit_Reset=now + 30, X_RateLimit_Remaining=0))
    expect(waits and 25 <= waits[0] <= 30, f"reset should be read, got {waits}")
    waits, _ = run(http(403, X_RateLimit_Reset=now + 3000, X_RateLimit_Remaining=42))
    expect(waits == [60], f"a reset with quota left is not a wait, got {waits}")


def a_stated_wait_is_capped():
    """A primary limit resets on the hour. Sleeping the cap and asking again is
    harmless; sleeping an hour inside one request is not."""
    now = int(github.time.time())
    waits, _ = run(http(403, X_RateLimit_Reset=now + 4000, X_RateLimit_Remaining=0))
    expect(waits == [github.MAX_WAIT], f"stated wait should cap, got {waits}")


def the_ladders_do_not_spend_each_other():
    """A transient error must not consume the patience the limiter is owed.
    Both ladders start from the top here, so the 403 waits its full minute
    even though two 502s came first."""
    waits, out = run(http(502), http(502), http(403))
    expect(waits == [2, 8, 60], f"ladders should be independent, got {waits}")
    expect(out == OK, f"the request should still succeed, got {out}")


def a_persistent_failure_gives_up_rather_than_hanging():
    """Nothing catches RateLimited, so this fails the build -- the correct
    outcome, and one that has to arrive in bounded time."""
    waits, out = run(*[http(502)] * 20)
    expect(isinstance(out, github.RateLimited), f"should give up, got {out}")
    expect(waits == list(github.TRANSIENT_BACKOFF),
           f"should walk the ladder once, got {waits}")


def one_request_cannot_wait_forever():
    """Two ladders and a stated wait can outlast either ladder alone: each
    class resets the other's count, and a limiter asking for the cap every time
    stretches the rest. The total is what actually stops it -- reached here
    while the rate-limit ladder still has an attempt left."""
    waits, out = run(*([http(403, Retry_After=600), http(502)] * 10))
    expect(isinstance(out, github.RateLimited), f"should give up, got {out}")
    expect("waiting" in str(out), f"should say why it stopped, got {out}")
    expect(sum(waits) <= github.MAX_TOTAL_WAIT,
           f"total waiting should be bounded, got {sum(waits)}s")


def an_error_that_waiting_cannot_fix_is_raised_at_once():
    """A 404 or a 422 is an answer, not an outage. Retrying one wastes the
    build's time and hides the mistake that caused it."""
    waits, out = run(http(404))
    expect(isinstance(out, urllib.error.HTTPError) and out.code == 404,
           f"a 404 should be raised, got {out}")
    expect(waits == [], f"a 404 should not be waited on, got {waits}")


def a_network_error_keeps_its_own_ladder():
    """Neither an outage nor a limit: a dropped connection is worth a short
    wait, and its own count, so it cannot spend the other two."""
    waits, out = run(urllib.error.URLError("timed out"))
    expect(waits == [10], f"a network error waits briefly, got {waits}")
    expect(out == OK, f"the retry should succeed, got {out}")


CASES = [
    ("a transient error is answered in seconds", a_transient_error_is_answered_in_seconds),
    ("a rate limit is answered in minutes", a_rate_limit_is_answered_in_minutes),
    ("the limiter is believed when it says how long", the_limiter_is_believed_when_it_says_how_long),
    ("a reset counts only once the budget is gone", a_reset_time_counts_only_once_the_budget_is_gone),
    ("a stated wait is capped", a_stated_wait_is_capped),
    ("the ladders do not spend each other", the_ladders_do_not_spend_each_other),
    ("a persistent failure gives up rather than hanging", a_persistent_failure_gives_up_rather_than_hanging),
    ("one request cannot wait forever", one_request_cannot_wait_forever),
    ("an error waiting cannot fix is raised at once", an_error_that_waiting_cannot_fix_is_raised_at_once),
    ("a network error keeps its own ladder", a_network_error_keeps_its_own_ladder),
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
        print(f"\n{len(failures)}/{len(CASES)} backoff checks failed", file=sys.stderr)
        return 1
    print(f"\nBackoff ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
