"""Minimal GitHub API client. Standard library only, by design.

The whole plugin pipeline runs through one GraphQL query shape: search returns
repository metadata, the README blob and the root tree in a single round trip,
so 739 repositories cost roughly fifteen requests rather than fifteen hundred.
"""

import json
import os
import time
import urllib.error
import urllib.request

GRAPHQL_URL = "https://api.github.com/graphql"
REST_URL = "https://api.github.com"
USER_AGENT = "koreader-plugin-index"

# GitHub's search endpoints refuse to page past 1000 results. We stay clear of
# the wall rather than at it, because a bucket that grows past it between runs
# would silently truncate instead of failing.
SEARCH_CAP = 900

# One query returns everything the plugin pipeline needs about a repository.
# `root` is the repository root tree, expanded one level: it tells us whether
# the files KOReader loads sit at the top level or one directory down, which is
# the strongest available signal that a repository is a real KOReader plugin
# and not a fork or a stub. Half the repositories that carry a plugin keep it
# under `src/` or `plugin/`, so the unexpanded root missed them.
#
# `entries` is a plain list rather than a connection, so it takes no `first:`
# and costs nothing against the node limit. It does enlarge the response -- and
# measured on a page of twenty repositories, by 6.7%: 743 KB against 696 KB, of
# which the whole two-level tree is 67 KB. The rest is README bodies this query
# already carried, beside which a second level of filenames is noise.
SEARCH_QUERY = """
query($q: String!, $after: String) {
  rateLimit { remaining resetAt }
  search(query: $q, type: REPOSITORY, first: 20, after: $after) {
    repositoryCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Repository {
        nameWithOwner
        name
        owner { login }
        description
        url
        stargazerCount
        forkCount
        isFork
        isArchived
        pushedAt
        createdAt
        licenseInfo { spdxId }
        defaultBranchRef { name }
        repositoryTopics(first: 20) { nodes { topic { name } } }
        readme:      object(expression: "HEAD:README.md")  { ... on Blob { text byteSize } }
        readmeLower: object(expression: "HEAD:readme.md")  { ... on Blob { text byteSize } }
        readmePlain: object(expression: "HEAD:README")     { ... on Blob { text byteSize } }
        root: object(expression: "HEAD:") {
          ... on Tree {
            entries {
              name type
              object { ... on Tree { entries { name type } } }
            }
          }
        }
      }
    }
  }
}
"""

COUNT_QUERY = """
query($q: String!) {
  search(query: $q, type: REPOSITORY, first: 1) { repositoryCount }
}
"""

# Patch files are fetched a repository at a time, aliasing every file into one
# query. A repo with fourteen patches then costs one request instead of
# fourteen. Each file needs two things: the blob, and the date of the last
# commit that touched *that path* -- for a patch, staleness is a safety signal
# and a repo-level date hides it, since one active repo can hold a patch that
# has been dead for two years.
FILES_QUERY_HEAD = """
query($owner: String!, $name: String!) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
"""

FILES_QUERY_TAIL = """
  }
}
"""


def _gql_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class RateLimited(Exception):
    pass


# Two different failures arriving through the same door, and they want opposite
# things. A 403 or 429 is the rate limiter: waiting is the only thing that
# helps, a minute is the right order, and the response usually says exactly how
# long. A 502 or 503 is GitHub having a moment; it clears in seconds, and
# spending a minute on it is pure loss.
#
# They shared one ladder until a full build showed what that costs. 184
# requests go out, a handful 502 on any given night, and the run that measured
# this took eleven minutes where the work is six -- the difference being four
# or five transient errors, each answered with a minute of sleep.
#
# The transient ladder is longer as well as shorter: cheap retries can afford
# to be patient, and giving up is the expensive outcome. Nothing catches
# RateLimited, so exhausting a ladder fails the whole build.
LIMIT_BACKOFF = (60, 120, 180, 240)
TRANSIENT_BACKOFF = (2, 8, 20, 45, 60, 60)
NETWORK_BACKOFF = (10, 20, 30, 40)

# A wait the ladder cannot exceed, and a total no request may pass however its
# failures are mixed. Without the second, alternating error classes could keep
# a single request alive indefinitely, each class resetting the other.
MAX_WAIT = 300
MAX_TOTAL_WAIT = 20 * 60


def _rate_limit_wait(headers):
    """How long the rate limiter says to wait, if it says.

    Guessing is what the ladder is for; when the answer is in the response,
    using it is both faster and kinder than a fixed minute. `Retry-After` is
    the secondary-limit answer, `x-ratelimit-reset` the primary one -- and the
    latter only means anything once the remaining count is actually zero.
    """
    raw = (headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return int(raw)
    reset = (headers.get("X-RateLimit-Reset") or "").strip()
    if reset.isdigit() and (headers.get("X-RateLimit-Remaining") or "").strip() == "0":
        return max(int(reset) - int(time.time()), 0)
    return None


class Client:
    def __init__(self, token=None, verbose=True):
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.verbose = verbose
        self.requests = 0
        self.remaining = None

    # ---------------------------------------------------------------- transport

    def _post(self, url, payload):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Counted per class, so a transient error does not spend the patience
        # the rate limiter is owed, or the other way round.
        tries = {"limit": 0, "transient": 0, "network": 0}
        total = 0
        while True:
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    kind, ladder = "limit", LIMIT_BACKOFF
                    stated = _rate_limit_wait(exc.headers or {})
                elif exc.code in (502, 503):
                    kind, ladder, stated = "transient", TRANSIENT_BACKOFF, None
                else:
                    raise
                reason = f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                kind, ladder, stated = "network", NETWORK_BACKOFF, None
                reason = f"network error ({exc.reason})"

            attempt = tries[kind]
            if attempt >= len(ladder):
                raise RateLimited(
                    f"{reason}: gave up after {attempt} attempts")
            tries[kind] = attempt + 1
            # The stated wait can exceed the cap -- a primary limit resets on
            # the hour. Sleeping the cap and asking again is harmless: the
            # answer is another 403 and another wait, and the ladder still
            # bounds how long this can go on.
            wait = min(stated if stated is not None else ladder[attempt], MAX_WAIT)
            if total + wait > MAX_TOTAL_WAIT:
                raise RateLimited(
                    f"{reason}: {total + wait}s of waiting on one request")
            total += wait
            self._log(f"  {reason}, waiting {wait}s")
            time.sleep(wait)

    def graphql(self, query, variables):
        data = self._post(GRAPHQL_URL, {"query": query, "variables": variables})
        if "errors" in data:
            # A partial response still carries usable nodes; only a total
            # absence of data is fatal.
            messages = "; ".join(e.get("message", "?") for e in data["errors"])
            if not data.get("data"):
                raise RuntimeError(f"GraphQL failed: {messages}")
            self._log(f"  GraphQL warning: {messages}")
        return data["data"]

    def _log(self, message):
        if self.verbose:
            print(message, flush=True)

    # ------------------------------------------------------------------ search

    def count(self, query):
        data = self.graphql(COUNT_QUERY, {"q": query})
        return data["search"]["repositoryCount"]

    def _page_all(self, query):
        """Yield every repository for one query, following cursors."""
        after = None
        while True:
            data = self.graphql(SEARCH_QUERY, {"q": query, "after": after})
            search = data["search"]
            limit = data.get("rateLimit") or {}
            self.remaining = limit.get("remaining")

            for node in search["nodes"]:
                if node:
                    yield node

            page = search["pageInfo"]
            if not page["hasNextPage"]:
                return
            after = page["endCursor"]

    def search(self, base_query):
        """Enumerate a search that may exceed GitHub's 1000-result ceiling.

        The catalogue sat at 752 repositories in August 2026 and is growing,
        so the ceiling is a matter of when rather than whether. Splitting by
        star count keeps every bucket small; a bucket that still overflows is
        split again by creation year. Nothing here truncates quietly: a bucket
        that no split can bring under the cap raises instead.
        """
        total = self.count(base_query)
        self._log(f"  '{base_query}' -> {total} repos")
        if total <= SEARCH_CAP:
            yield from self._page_all(base_query)
            return

        self._log(f"  above {SEARCH_CAP}, splitting by stars")
        ladder = [(0, 0), (1, 1), (2, 3), (4, 7), (8, 15), (16, 31),
                  (32, 63), (64, 127), (128, 255), (256, None)]
        for lo, hi in ladder:
            span = f"stars:{lo}..{hi}" if hi is not None else f"stars:>={lo}"
            bucket = f"{base_query} {span}"
            if self.count(bucket) <= SEARCH_CAP:
                yield from self._page_all(bucket)
            else:
                yield from self._split_by_year(bucket)

    def _split_by_year(self, query):
        """Last resort: one sub-query per creation year.

        The first bucket is open-ended rather than starting at a fixed year.
        A hard floor silently drops everything older than it, and this branch
        only ever runs years from now, when nobody is watching the log.

        A year that still overflows is not split further, because reaching one
        would mean 900 repositories of one star count created in one calendar
        year -- an ecosystem two orders of magnitude larger than this one.
        Rather than carry a bisection that could never be tested, it fails and
        says what to widen.
        """
        self._log(f"  '{query}' still large, splitting by year")
        spans = [("created:<2017-01-01", "pre-2017")]
        spans += [(f"created:{y}-01-01..{y}-12-31", str(y))
                  for y in range(2017, time.gmtime().tm_year + 1)]
        for span, label in spans:
            sub = f"{query} {span}"
            found = self.count(sub)
            if found > SEARCH_CAP:
                raise RuntimeError(
                    f"'{sub}' -> {found} repos, past the {SEARCH_CAP} cap with "
                    "nothing left to split by. Widen the star ladder in "
                    "Client.search() or split this year by month.")
            if found:
                self._log(f"    {label}: {found}")
                yield from self._page_all(sub)

    # ------------------------------------------------------------------- files

    def fetch_files(self, owner, name, paths, batch=20, with_history=True):
        """Blob text plus last-commit date for each path, keyed by path.

        Batched because a single query with sixty aliases is both slow and
        easy for GitHub to reject; twenty files per request stays comfortable.

        `with_history` buys the per-path commit date, which for a patch file is
        a safety signal worth a second sub-query. A translated README is not
        judged on its own staleness -- the repository's push date already
        covers that -- so its caller turns the history off and halves the query.
        """
        result = {}
        for start in range(0, len(paths), batch):
            chunk = paths[start:start + batch]
            parts = []
            for i, path in enumerate(chunk):
                literal = _gql_string(f"HEAD:{path}")
                parts.append(
                    f'    f{i}: object(expression: {literal}) '
                    f'{{ ... on Blob {{ text byteSize oid }} }}'
                )
                if with_history:
                    parts.append(
                        f'    h{i}: defaultBranchRef {{ target {{ ... on Commit {{ '
                        f'history(first: 1, path: {_gql_string(path)}) '
                        f'{{ nodes {{ committedDate }} }} }} }} }}'
                    )
            query = FILES_QUERY_HEAD + "\n".join(parts) + FILES_QUERY_TAIL

            data = self.graphql(query, {"owner": owner, "name": name})
            repo = (data or {}).get("repository") or {}
            limit = (data or {}).get("rateLimit") or {}
            self.remaining = limit.get("remaining", self.remaining)

            for i, path in enumerate(chunk):
                blob = repo.get(f"f{i}")
                if not blob or blob.get("text") is None:
                    continue
                history = ((repo.get(f"h{i}") or {}).get("target") or {}).get("history") or {}
                nodes = history.get("nodes") or []
                result[path] = {
                    "text": blob["text"],
                    "bytes": blob.get("byteSize", 0),
                    "sha": blob.get("oid"),
                    "modified_at": nodes[0]["committedDate"] if nodes else None,
                }
        return result

    # -------------------------------------------------------------------- rest

    def get_json(self, path):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(REST_URL + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.requests += 1
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            return None


def fetch_url(url, timeout=30):
    """Plain GET for the previously published index, which doubles as state."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
