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
# `root` is the repository root tree: it tells us whether _meta.lua sits at the
# top level or inside a *.koplugin directory, which is the strongest available
# signal that a repository is a real KOReader plugin and not a fork or a stub.
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
        licenseInfo { spdxId }
        defaultBranchRef { name }
        repositoryTopics(first: 20) { nodes { topic { name } } }
        readme:      object(expression: "HEAD:README.md")  { ... on Blob { text byteSize } }
        readmeLower: object(expression: "HEAD:readme.md")  { ... on Blob { text byteSize } }
        readmePlain: object(expression: "HEAD:README")     { ... on Blob { text byteSize } }
        root: object(expression: "HEAD:") {
          ... on Tree { entries { name type } }
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


class RateLimited(Exception):
    pass


class Client:
    def __init__(self, token=None, verbose=True):
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.verbose = verbose
        self.requests = 0
        self.remaining = None

    # ---------------------------------------------------------------- transport

    def _post(self, url, payload, retries=4):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        for attempt in range(retries):
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # 403/429 is the rate limiter; 5xx is GitHub having a moment.
                # Both are worth waiting out, and neither should lose the run.
                if exc.code in (403, 429, 502, 503):
                    wait = min(60 * (attempt + 1), 300)
                    self._log(f"  HTTP {exc.code}, waiting {wait}s")
                    time.sleep(wait)
                    continue
                raise
            except urllib.error.URLError as exc:
                wait = 10 * (attempt + 1)
                self._log(f"  network error ({exc.reason}), waiting {wait}s")
                time.sleep(wait)
        raise RateLimited(f"gave up after {retries} attempts")

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

        The catalogue sat at 739 repositories in August 2026 and is growing,
        so the ceiling is a matter of when rather than whether. Splitting by
        star count keeps every bucket small; a bucket that still overflows is
        split again by creation year.
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
        self._log(f"  '{query}' still large, splitting by year")
        for year in range(2017, time.gmtime().tm_year + 1):
            sub = f"{query} created:{year}-01-01..{year}-12-31"
            if self.count(sub):
                yield from self._page_all(sub)

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
