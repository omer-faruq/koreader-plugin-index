"""How a hand-named repository enters the catalogue.

Discovery is four search queries, and a plugin whose author used neither the
`koreader-plugin` topic nor ".koplugin" in the repository name is returned by
none of them -- not ranked low, absent. `curation.toml` names those outright
under `[discovery]`, and `add_seeds` asks GitHub for them by name.

Two things about that path are easy to break without anything failing. It is
batched with aliases, so a boundary error silently drops the twenty-first seed
and no test of one seed would notice. And a seed is one stranger's repository:
when it is deleted, renamed or made private the alias comes back null beside an
error message, and the run has to carry on -- including for the other seeds in
the same batch, which is exactly what a naive "no data, give up" would lose.

Offline: the transport is stubbed, and the node below is the shape SEARCH_QUERY
returns.
"""

import datetime
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402
import github  # noqa: E402

ALIAS = re.compile(r'r(\d+): repository\(owner: "([^"]*)", name: "([^"]*)"\)')


# Pushed last week rather than on a fixed date: tier_of reads freshness off
# this, so a hardcoded timestamp would quietly turn the last check below into a
# test that the calendar has not moved.
RECENT = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

# Over STUB_README_BYTES, so the entry counts as documented rather than a stub.
# Where that line falls is extract.py's business; this test only needs to be on
# the ordinary side of it.
README = ("# Focus Ruler\n\nA KOReader plugin that improves reading focus by "
          "highlighting only a few lines at a time, with the rest of the page "
          "whited out.\n\n## Installation\n\nCopy the folder into the KOReader "
          "plugins directory and restart.\n\n## Usage\n\nTools -> Focus Ruler -> "
          "Toggle focus ruler. Tap to move to the next line, or swipe up.\n")


def node(full):
    """A search node, cut down to the fields this path actually reads."""
    owner, _, name = full.partition("/")
    return {
        "nameWithOwner": full,
        "name": name,
        "owner": {"login": owner},
        "description": "",
        "url": f"https://github.com/{full}",
        "stargazerCount": 5,
        "forkCount": 0,
        "isFork": False,
        "isArchived": False,
        "pushedAt": RECENT,
        "createdAt": "2025-01-01T00:00:00Z",
        "licenseInfo": {"spdxId": "MIT"},
        "defaultBranchRef": {"name": "main"},
        "repositoryTopics": {"nodes": []},
        "readme": {"text": README, "byteSize": len(README.encode("utf-8"))},
        "readmeLower": None,
        "readmePlain": None,
        "root": {"entries": [{"name": "main.lua", "type": "blob"},
                             {"name": "_meta.lua", "type": "blob"}]},
    }


class StubClient(github.Client):
    """A real Client with only the transport replaced.

    Every alias naming a repository in `known` answers with a node; the rest
    answer null, the way GitHub answers one that is gone.
    """

    def __init__(self, known=()):
        self.known = set(known)
        self.calls = []
        self.remaining = None
        self.verbose = False

    def graphql(self, query, variables):
        aliases = ALIAS.findall(query)
        self.calls.append([f"{owner}/{name}" for _, owner, name in aliases])
        data = {"rateLimit": {"remaining": 4999, "resetAt": None}}
        for i, owner, name in aliases:
            full = f"{owner}/{name}"
            data[f"r{i}"] = node(full) if full in self.known else None
        return data


def seeds(*names):
    return {"discovery": {"extra_plugins": list(names)}}


SEED = "iamnotwassim/focusruler"


def one_request_for_one_seed():
    client, found = StubClient([SEED]), {}
    build.add_seeds(client, seeds(SEED), found)
    assert client.calls == [[SEED]], client.calls
    assert list(found) == [SEED], found


def already_discovered_is_not_fetched():
    """The author added the topic. The seed line is now dead weight, not work."""
    client, found = StubClient([SEED]), {SEED: node(SEED)}
    build.add_seeds(client, seeds(SEED), found)
    assert client.calls == [], client.calls


def batches_of_twenty():
    many = [f"owner{i}/repo{i}" for i in range(45)]
    client, found = StubClient(many), {}
    build.add_seeds(client, seeds(*many), found)
    sizes = [len(call) for call in client.calls]
    assert sizes == [20, 20, 5], sizes
    assert len(found) == 45, len(found)
    assert list(found) == many, "batching must not reorder the list"


def a_seed_that_has_gone_is_skipped():
    client, found = StubClient([]), {}
    build.add_seeds(client, seeds("nobody/gone"), found)
    assert found == {}, found


def a_gone_seed_does_not_take_its_batch_down():
    client, found = StubClient([SEED]), {}
    build.add_seeds(client, seeds("nobody/gone", SEED), found)
    assert client.calls == [["nobody/gone", SEED]], client.calls
    assert list(found) == [SEED], found


def a_malformed_line_is_skipped():
    client, found = StubClient([SEED]), {}
    build.add_seeds(client, seeds("bad-shape", SEED), found)
    assert client.calls == [[SEED]], client.calls
    assert list(found) == [SEED], found


def no_discovery_table_is_not_an_error():
    client, found = StubClient([]), {}
    build.add_seeds(client, {}, found)
    assert client.calls == [] and found == {}


def a_seed_is_built_like_anything_else():
    """No downstream rule may learn where an entry came from."""
    entry, _, _ = build.build_plugin(node(SEED), build.load_curation())
    assert entry["id"] == SEED, entry["id"]
    assert entry["has_plugin_files"] is True, entry
    assert entry["tier"] == "B", entry["tier"]
    assert "curated" not in entry["tier_reasons"], entry["tier_reasons"]


CASES = [
    ("one request for one seed", one_request_for_one_seed),
    ("already discovered is not fetched", already_discovered_is_not_fetched),
    ("batches of twenty, in order", batches_of_twenty),
    ("a seed that has gone is skipped", a_seed_that_has_gone_is_skipped),
    ("a gone seed does not take its batch down", a_gone_seed_does_not_take_its_batch_down),
    ("a malformed line is skipped", a_malformed_line_is_skipped),
    ("no discovery table is not an error", no_discovery_table_is_not_an_error),
    ("a seed is built like anything else", a_seed_is_built_like_anything_else),
]


def main():
    print("Discovery seeds")
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
        print(f"\n{len(failures)}/{len(CASES)} discovery seed checks failed", file=sys.stderr)
        return 1
    print(f"\nDiscovery seeds ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
