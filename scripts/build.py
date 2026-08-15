"""Build the published index.

    python scripts/build.py --mode diff    # daily: only what changed
    python scripts/build.py --mode full    # monthly, or after a rule change

Diff mode asks GitHub for repositories pushed since the last run and carries
every untouched entry over from the previously published index, so a normal day
costs about ten requests. There is no "what changed in this topic" endpoint on
GitHub; comparing pushed_at against the last published index is the diff, and
that index doubles as the state file so nothing extra has to be persisted.
"""

import argparse
import datetime
import json
import os
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import extract  # noqa: E402
from github import Client, fetch_url  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DETAIL = DOCS / "detail"

SCHEMA_VERSION = 2
SOURCE_REPO = "https://github.com/omer-faruq/koreader-plugin-index"
PUBLISHED_INDEX = "https://omer-faruq.github.io/koreader-plugin-index/index.json"
READMES_URL = "https://omer-faruq.github.io/koreader-plugin-index/readme-index.json"

# Discovery mirrors the AppStore page's QUERIES.plugins so the index cannot
# drift from what the plugin itself finds. The quoting matters: `in:name
# ".koplugin"` and `koplugin in:name` are different searches, and the loose
# form returned 86 fewer repositories than the AppStore reports.
#
# Two passes per surface, because GitHub cannot express "non-forks, plus forks
# somebody starred" in a single query:
#
#   bare query      -> non-forks only, which is GitHub's default
#   fork:only ...   -> forks, restricted to those with at least one star
#
# Both halves are needed. Forks are excluded by default, and in this ecosystem
# plugins routinely start as a fork -- assistant.koplugin (574 stars),
# rakuyomi (505) and localsend.koplugin (255) are all forks and were missing
# from the index entirely. But `fork:true` overshoots: it pulled the name query
# from 572 repositories to 1456, almost all of them unstarred copies that tier
# C would hide anyway, at the cost of a README fetch each. Requiring a star is
# the same line the AppStore page draws by default with `zeroStarForks: false`.
PLUGIN_QUERIES = [
    "topic:koreader-plugin",
    "topic:koreader-plugin fork:only stars:>=1",
    'in:name ".koplugin"',
    'in:name ".koplugin" fork:only stars:>=1',
]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------- curation

def load_curation():
    path = ROOT / "curation.toml"
    if not path.exists():
        return {"plugins": {}, "patches": {}, "distinctions": []}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        "plugins": data.get("plugins", {}),
        "patches": data.get("patches", {}),
        "distinctions": data.get("distinctions", []),
    }


def apply_curation(entry, curated):
    """Hand-written judgement wins over anything a rule produced.

    This is the whole reason a weekly rebuild is safe: the distinctions and
    cautions a README cannot yield are the most valuable part of the catalogue,
    and a naive regeneration would erase them.
    """
    if not curated:
        return entry
    for field in ("purpose", "note"):
        if curated.get(field):
            entry[field] = curated[field]
    if curated.get("categories"):
        entry["categories"] = list(curated["categories"])
    if curated.get("keywords"):
        merged = list(entry.get("keywords", []))
        for keyword in curated["keywords"]:
            if keyword not in merged:
                merged.append(keyword)
        entry["keywords"] = merged
    return entry


# --------------------------------------------------------------------- plugins

def readme_of(node):
    for key in ("readme", "readmeLower", "readmePlain"):
        blob = node.get(key)
        if blob and blob.get("text"):
            return blob["text"], blob.get("byteSize", 0)
    return "", 0


def has_plugin_marker(node):
    """_meta.lua at the root, or a *.koplugin directory holding one.

    Repository layout is not consistent: some repositories are the plugin
    folder, others contain it. Both are real, and missing the second shape
    would demote a large number of legitimate plugins.
    """
    root = node.get("root") or {}
    entries = root.get("entries") or []
    for item in entries:
        name = item.get("name", "")
        if name == "_meta.lua":
            return True
        if item.get("type") == "tree" and name.endswith(".koplugin"):
            return True
    return False


def build_plugin(node, curation):
    readme, readme_bytes = readme_of(node)
    topics = [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]]
    headings = extract.extract_headings(readme)
    description = node.get("description") or ""

    # README body terms feed the lightweight index too, not just the deep
    # search file: the sentence that says what a plugin does is usually well
    # past the first paragraph.
    body_terms = extract.readme_terms(readme)

    entry = {
        "id": node["nameWithOwner"],
        "owner": node["owner"]["login"],
        "repo": node["name"],
        "url": node["url"],
        "description": description,
        "purpose": extract.extract_purpose(readme),
        # Deliberately without body_terms. Feeding them in took `misc` from 33%
        # to 9%, but tagged 49% of the catalogue `ui` and 46% `files`: every
        # README says "copy the files" somewhere, and a chip matching half the
        # catalogue tells a reader nothing. Categories come from the fields
        # that state identity; body terms stay in keywords, where breadth is
        # exactly what search wants.
        "categories": extract.categorise(
            " ".join([description] + headings), topics, node["name"]
        ),
        "keywords": extract.keywords(description, topics, headings, extra=body_terms),
        "topics": topics,
        "stars": node["stargazerCount"],
        "forks": node["forkCount"],
        "is_fork": node["isFork"],
        "archived": node["isArchived"],
        "pushed_at": node["pushedAt"],
        "license": (node.get("licenseInfo") or {}).get("spdxId"),
        "default_branch": (node.get("defaultBranchRef") or {}).get("name") or "main",
        "has_meta": has_plugin_marker(node),
        "readme_bytes": readme_bytes,
        "detail": None,
    }
    entry["activity"] = extract.activity_of(entry["pushed_at"], entry["archived"])

    curated = curation["plugins"].get(entry["id"], {})
    entry = apply_curation(entry, curated)
    entry["tier"], entry["tier_reasons"] = extract.tier_of(entry, curated.get("tier"))

    detail = None
    if readme:
        detail = {
            "id": entry["id"],
            "headings": headings,
            "readme_excerpt": extract.clean_markdown(readme)[:4000],
        }
        entry["detail"] = f"detail/{entry['owner']}__{entry['repo']}.json"
    return entry, detail, extract.condense_readme(readme)


# ---------------------------------------------------------------------- patches

# Mirrors the AppStore page's QUERIES.patches, with the same two-pass fork
# handling as plugins.
PATCH_QUERIES = [
    "topic:koreader-user-patch",
    "topic:koreader-user-patch fork:only stars:>=1",
    'in:name "KOReader.patches"',
    'in:name "KOReader.patches" fork:only stars:>=1',
]


def patch_paths(node):
    """Root-level `N-name.lua` files.

    Root only, which is where the convention puts them and what the AppStore
    itself enumerates -- confirmed against the three largest patch repos.
    """
    root = node.get("root") or {}
    names = []
    for item in root.get("entries") or []:
        if item.get("type") != "blob":
            continue
        order, _ = extract.parse_patch_name(item.get("name", ""))
        if order is not None:
            names.append(item["name"])
    return sorted(names)


def build_patch(node, path, blob, curation):
    owner = node["owner"]["login"]
    repo = node["name"]
    branch = (node.get("defaultBranchRef") or {}).get("name") or "main"
    order, label = extract.parse_patch_name(path)

    purpose = extract.extract_patch_purpose(blob.get("text", ""))
    # The repository description is a weak fallback and deliberately not used
    # as a per-patch purpose: one README describes fourteen patches at once and
    # names none of them.
    text_pool = " ".join([label or "", purpose])

    entry = {
        "id": f"{owner}/{repo}:{path}",
        "owner": owner,
        "repo": repo,
        "path": path,
        "order": order,
        "url": f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
        "raw_url": f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}",
        "purpose": purpose,
        "categories": extract.categorise(text_pool, node_topics(node), label or path),
        "keywords": extract.keywords(label or "", node_topics(node), [purpose]),
        "file_sha": blob.get("sha"),
        "file_modified_at": blob.get("modified_at"),
        "file_bytes": blob.get("bytes", 0),
        "repo_stars": node["stargazerCount"],
        "repo_pushed_at": node["pushedAt"],
        "archived": node["isArchived"],
    }
    # Freshness is the file's, not the repository's.
    entry["activity"] = extract.activity_of(
        entry["file_modified_at"] or entry["repo_pushed_at"], entry["archived"]
    )

    curated = curation["patches"].get(entry["id"], {})
    entry = apply_curation(entry, curated)
    entry["tier"], entry["tier_reasons"] = extract.tier_of_patch(entry, curated.get("tier"))
    return entry


def node_topics(node):
    return [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]]


def collect_patches(client, curation, since=None):
    repos = {}
    for base in PATCH_QUERIES:
        query = base + (f" pushed:>{since}" if since else "")
        for node in client.search(query):
            repos[node["nameWithOwner"]] = node

    entries, touched_repos = [], 0
    for node in repos.values():
        paths = patch_paths(node)
        if not paths:
            continue
        touched_repos += 1
        blobs = client.fetch_files(node["owner"]["login"], node["name"], paths)
        for path in paths:
            blob = blobs.get(path)
            if blob:
                entries.append(build_patch(node, path, blob, curation))
    return entries, touched_repos, len(repos)


def collect_plugins(client, since=None):
    """Run every discovery query, de-duplicating by repository id."""
    found = {}
    for base in PLUGIN_QUERIES:
        query = base
        if since:
            query += f" pushed:>{since}"
        for node in client.search(query):
            found[node["nameWithOwner"]] = node
    return found


# ----------------------------------------------------------------------- output

def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    return path.stat().st_size


def category_summary(entries):
    counts = {}
    for entry in entries:
        for cid in entry.get("categories", []):
            counts[cid] = counts.get(cid, 0) + 1
    return [
        {"id": cid, "label": extract.CATEGORY_LABELS.get(cid, cid), "count": counts[cid]}
        for cid in extract.CATEGORY_LABELS
        if cid in counts
    ]


def sanity_check(plugins, previous):
    """Refuse to publish a catalogue that collapsed.

    A bad hour at the GitHub API must not be able to empty the index; the last
    good publish is a better answer than a truncated one.
    """
    if not previous:
        return True, ""
    before = len(previous.get("plugins", []))
    after = len(plugins)
    if before and after < before * 0.7:
        return False, f"plugin count dropped {before} -> {after} (>30%)"
    return True, ""


# ------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "diff"], default="diff")
    parser.add_argument("--since-days", type=int, default=2,
                        help="diff window; wider than the daily cadence on purpose")
    parser.add_argument("--out", default=str(DOCS))
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out)
    client = Client()
    curation = load_curation()
    started = now_iso()

    previous = fetch_url(PUBLISHED_INDEX)
    if previous:
        print(f"previous index: {len(previous.get('plugins', []))} plugins "
              f"({previous.get('generated_at')})")
    elif args.mode == "diff":
        print("no previous index reachable, falling back to a full build")
        args.mode = "full"

    since = None
    if args.mode == "diff":
        window = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.since_days)
        since = window.strftime("%Y-%m-%d")
        print(f"diff mode: repositories pushed since {since}")
    else:
        print("full mode: enumerating everything")

    print("collecting plugins…")
    nodes = collect_plugins(client, since)
    print(f"  {len(nodes)} repositories returned")

    entries, details, readmes = {}, {}, {}

    # Deep search reuses whatever the previous run condensed, so a diff run
    # does not lose README text for the repositories it did not touch.
    if args.mode == "diff":
        previous_readmes = fetch_url(READMES_URL) or {}
        readmes.update(previous_readmes.get("readmes", {}))

    # Carry forward everything the diff did not touch. Untouched entries keep
    # their previously extracted fields; only curation is re-applied, so a
    # curation.toml edit takes effect on the next run without a full rebuild.
    if args.mode == "diff" and previous:
        for old in previous.get("plugins", []):
            if old["id"] in nodes:
                continue
            curated = curation["plugins"].get(old["id"], {})
            old = apply_curation(old, curated)
            old["tier"], old["tier_reasons"] = extract.tier_of(old, curated.get("tier"))
            entries[old["id"]] = old

    for node in nodes.values():
        entry, detail, condensed = build_plugin(node, curation)
        entries[entry["id"]] = entry
        if detail:
            details[entry["id"]] = detail
        if condensed:
            readmes[entry["id"]] = condensed

    plugins = sorted(entries.values(), key=lambda e: (-e["stars"], e["id"].lower()))

    print("collecting patches…")
    patch_entries, patch_repos, patch_repos_seen = collect_patches(client, curation, since)
    # A diff run only sees repositories pushed recently, so patches from
    # untouched repos are carried over the same way plugins are.
    if args.mode == "diff" and previous:
        have = {p["id"] for p in patch_entries}
        for old in previous.get("patches", []):
            if old["id"] not in have:
                patch_entries.append(old)
        patch_repos = max(patch_repos, previous.get("counts", {}).get("patch_repos", 0))
    patches = sorted(patch_entries, key=lambda e: (-e["repo_stars"], e["id"].lower()))
    print(f"  {len(patches)} patch files across {patch_repos} repos "
          f"({patch_repos_seen} matched the search)")

    ok, why = sanity_check(plugins, previous)
    if not ok:
        print(f"ABORT: {why}", file=sys.stderr)
        return 1

    index = {
        "schema": SCHEMA_VERSION,
        "generated_at": started,
        "source_repo": SOURCE_REPO,
        "build_mode": args.mode,
        "counts": {
            "plugins": len(plugins),
            "patches": len(patches),
            "patch_repos": patch_repos,
        },
        "categories": category_summary(plugins + patches),
        "distinctions": curation["distinctions"],
        "plugins": plugins,
        # Patches live in their own file from schema 2 on. Inlining them took
        # index.json past a megabyte, and the patches tab is opt-in the same
        # way deep search is -- most visitors never open it, and the device
        # should not pay for it on every refresh.
        "patches_url": "patches.json",
    }

    size = write_json(out_dir / "index.json", index)
    patch_size = write_json(out_dir / "patches.json", {
        "schema": SCHEMA_VERSION,
        "generated_at": started,
        "counts": {"patches": len(patches), "patch_repos": patch_repos},
        "patches": patches,
    })

    # Kept out of index.json deliberately. Inlining 740 condensed READMEs would
    # multiply the file the device downloads on every refresh, for a search
    # mode most users never turn on. The page fetches this only when asked.
    live = {pid: text for pid, text in readmes.items() if pid in entries}
    readme_size = write_json(out_dir / "readme-index.json", {
        "schema": SCHEMA_VERSION,
        "generated_at": started,
        "readmes": live,
    })

    for detail in details.values():
        owner, repo = detail["id"].split("/", 1)
        write_json(out_dir / "detail" / f"{owner}__{repo}.json", detail)

    tiers = {}
    for entry in plugins:
        tiers[entry["tier"]] = tiers.get(entry["tier"], 0) + 1
    misc = sum(1 for e in plugins if e["categories"] == ["misc"])

    print(f"\nwrote {out_dir/'index.json'} ({size/1024:.0f} KB)")
    print(f"  plugins   {len(plugins)}")
    print(f"  tiers     " + "  ".join(f"{k}:{v}" for k, v in sorted(tiers.items())))
    print(f"  misc-only {misc} ({misc*100//max(len(plugins),1)}%)")
    print(f"  details   {len(details)}")
    ptiers = {}
    for entry in patches:
        ptiers[entry["tier"]] = ptiers.get(entry["tier"], 0) + 1
    no_header = sum(1 for e in patches if not e["purpose"])
    print(f"  patches   {len(patches)} in {patch_repos} repos ({patch_size/1024:.0f} KB)")
    print(f"    tiers   " + "  ".join(f"{k}:{v}" for k, v in sorted(ptiers.items())))
    print(f"    no doc  {no_header}")
    print(f"  requests  {client.requests}  (rate limit left: {client.remaining})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
