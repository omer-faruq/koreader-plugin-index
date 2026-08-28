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
import knowledge_base  # noqa: E402
import seo  # noqa: E402
from github import Client, fetch_url  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DETAIL = DOCS / "detail"

SCHEMA_VERSION = 2
SOURCE_REPO = "https://github.com/omer-faruq/koreader-plugin-index"
PUBLISHED_INDEX = "https://omer-faruq.github.io/koreader-plugin-index/index.json"
READMES_URL = "https://omer-faruq.github.io/koreader-plugin-index/readme-index.json"
DETAILS_URL = "https://omer-faruq.github.io/koreader-plugin-index/details.json"
PATCHES_URL = "https://omer-faruq.github.io/koreader-plugin-index/patches.json"
PAGES_BASE = "https://omer-faruq.github.io/koreader-plugin-index"
APPSTORE_URL = "https://omer-faruq.github.io/appstore.koplugin/"

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
        # Longest key first, so 微信读书 is considered before 读书. Both may
        # match and both labels are true, but the order keeps the specific
        # label ahead of the general one in a list that gets truncated.
        "glossary": dict(sorted(data.get("glossary", {}).items(),
                                key=lambda kv: -len(kv[0]))),
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


def attach_english_readmes(client, nodes):
    """Fetch the translated README of repositories that document in Chinese.

    Ranking tokenises on [a-z0-9]+, so a Chinese README scores zero against
    every query and its plugin reaches the AI shortlist only through the tier
    filler -- which, for the two-star repositories this mostly affects, means
    never. Where the repository publishes its own translation, that is the
    whole problem solved with the repository's own words rather than a guess.

    Detection is free: the root tree is already in the search response. Only
    the few repositories that actually carry a sidecar cost a request, and in
    August 2026 that was six out of 752.
    """
    wanted = []
    for node in nodes.values():
        readme, _ = readme_of(node)
        if not readme or extract.cjk_ratio(readme) < extract.CJK_DOMINANT:
            continue
        entries = (node.get("root") or {}).get("entries") or []
        name = extract.english_readme_name(
            [e.get("name", "") for e in entries if e.get("type") == "blob"])
        if name:
            wanted.append((node, name))

    if not wanted:
        return
    print(f"  {len(wanted)} non-English READMEs have a translation beside them")
    for node, name in wanted:
        blobs = client.fetch_files(node["owner"]["login"], node["name"],
                                   [name], with_history=False)
        blob = blobs.get(name)
        if blob and blob.get("text"):
            node["readmeEnglish"] = blob["text"]
            node["readmeEnglishName"] = name
            print(f"    {node['nameWithOwner']}: {name}")


# What KOReader itself loads. `pluginloader.lua` discovers directories whose
# name ends in `.koplugin`, then runs `main.lua` inside one; a plugin missing
# that file is logged as an error and skipped. `_meta.lua` is read separately,
# under its own pcall, and only merged if it parses -- a plugin without one
# loads and runs exactly the same. It matters in one place only: a *disabled*
# plugin is read from `_meta.lua` instead, so without it the plugin-management
# menu has no name or description to show. Cosmetic, not functional.
#
# This file used to require `_meta.lua`, which is why a third of the demoted
# entries were real plugins. Verified on a device: they install and run.
LOADABLE = ("main.lua", "_meta.lua")


def _is_marker(name, kind):
    return name in LOADABLE or (kind == "tree" and name.endswith(".koplugin"))


def has_plugin_marker(node):
    """Does this repository hold something KOReader would load?

    Two shapes, and repository layout is not consistent about which: some
    repositories *are* the plugin folder, and some contain it, one level down
    under `src/`, `plugin/`, `plugins/`, `apps/`, `dist/` or a name nobody has
    used yet. Looking only at the root missed the second shape entirely and
    demoted 34 of the 75 entries it was applied to -- among them plugins with
    hundreds of stars, hidden behind a checkbox reading "dormant, forks and
    stubs".

    One level is as deep as this goes. A `main.lua` further down is more likely
    to belong to something else in the repository than to be the plugin, and
    the shapes actually in use are all reachable at depth one.

    Purely additive: everything the root rule accepted, this accepts. No entry
    can be demoted by looking deeper, only promoted.
    """
    entries = (node.get("root") or {}).get("entries") or []
    for item in entries:
        name, kind = item.get("name", ""), item.get("type")
        if _is_marker(name, kind):
            return True
        if kind != "tree":
            continue
        # Absent when the tree was not expanded -- an older cached response, or
        # a directory GitHub declined to walk. Treated as "nothing found here"
        # rather than as an error: the root rule above still stands on its own.
        below = (item.get("object") or {}).get("entries") or []
        for child in below:
            if _is_marker(child.get("name", ""), child.get("type")):
                return True
    return False


def build_plugin(node, curation):
    readme, readme_bytes = readme_of(node)

    # Every extracted field below comes from the English view of the README
    # rather than the README itself: the translation beside it where there is
    # one, the English sections of a bilingual document where there are any,
    # and otherwise the original unchanged. The excerpt panel further down
    # follows the same rule with one boundary of its own, explained there.
    sidecar = node.get("readmeEnglish", "")
    source = extract.english_view(readme, sidecar)
    features = extract.extract_features(source)

    # Filtering can leave less than it found. A bilingual README whose English
    # side is all headings, tables and shell commands yields nothing at all,
    # and publishing less than the previous run is not an improvement, so a
    # view that produces neither purpose nor features hands the document back.
    if not features and not extract.extract_purpose(source):
        source = readme
        features = extract.extract_features(source)

    # The purpose falls back on its own, and separately. An English side made
    # of bullets alone yields features but no opening prose, and returning the
    # whole document there would throw those features away to recover a
    # sentence no English query can reach. Splitting the two took the plugins
    # left with no purpose at all from seven to none.
    purpose_from_source = (extract.extract_purpose(source)
                           or extract.extract_purpose(readme))

    topics = [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]]
    headings = extract.extract_headings(source)
    description = node.get("description") or ""

    # README body terms feed the lightweight index too, not just the deep
    # search file: the sentence that says what a plugin does is usually well
    # past the first paragraph.
    body_terms = extract.readme_terms(source)
    # Where a document says what it *is*: the opening prose and the headings.
    # Run over the whole README instead, one passing mention of 漫画 in a long
    # Chinese document made that plugin a comic reader, and legado.koplugin --
    # which mentions nearly everything -- outranked comicreader.koplugin on
    # "comic reader". The same reasoning already keeps body terms out of
    # categorisation a few lines below.
    identity = " ".join([extract.extract_purpose(readme) or ""]
                        + extract.extract_headings(readme))
    glossed = extract.glossary_keywords(identity, curation.get("glossary", {}))

    # A Chinese purpose beside an English repository description is the one
    # case where the weaker source is the better one. Every consumer reads
    # `purpose or description` and stops at the first, so a good English
    # sentence in the About field was being hidden behind text no English
    # query could retrieve and no English reader could use. Nine plugins were
    # in that state, several of them with a description better than anything
    # extraction could have produced: "Turn your KOReader device into a file
    # server (HTTP + WebDAV + FTP)".
    purpose = purpose_from_source
    if (extract.cjk_ratio(purpose) >= extract.CJK_DOMINANT
            and description
            and extract.cjk_ratio(description) < extract.CJK_DOMINANT):
        purpose = description

    entry = {
        "id": node["nameWithOwner"],
        "owner": node["owner"]["login"],
        "repo": node["name"],
        "url": node["url"],
        "description": description,
        "purpose": purpose,
        # Deliberately without body_terms. Feeding them in took `misc` from 33%
        # to 9%, but tagged 49% of the catalogue `ui` and 46% `files`: every
        # README says "copy the files" somewhere, and a chip matching half the
        # catalogue tells a reader nothing. Categories come from the fields
        # that state identity; body terms stay in keywords, where breadth is
        # exactly what search wants.
        "categories": extract.categorise(
            " ".join([description] + extract.feature_headings(headings)),
            topics, node["name"]
        ),
        # What a plugin can actually do usually lives in the feature list, not
        # the opening slogan. Carried in the index so every consumer gets it.
        "features": features,
        # The glossary reaches the original README rather than the English
        # view: it exists precisely for the documents no view could make
        # English, and matches nothing in one that already is.
        "keywords": extract.keywords(description, topics, headings,
                                     extra=body_terms + features + glossed),
        "topics": topics,
        "stars": node["stargazerCount"],
        "forks": node["forkCount"],
        "is_fork": node["isFork"],
        "archived": node["isArchived"],
        "pushed_at": node["pushedAt"],
        # Free: the same query already returns it. What it buys is the one
        # question the index could not answer -- what is new here -- which for
        # a catalogue growing by a few plugins a week is most of the reason to
        # come back to it.
        "created_at": node.get("createdAt"),
        "license": (node.get("licenseInfo") or {}).get("spdxId"),
        "default_branch": (node.get("defaultBranchRef") or {}).get("name") or "main",
        "has_plugin_files": has_plugin_marker(node),
        "readme_bytes": readme_bytes,
        "detail": None,
    }
    entry["activity"] = extract.activity_of(entry["pushed_at"], entry["archived"])

    curated = curation["plugins"].get(entry["id"], {})
    entry = apply_curation(entry, curated)
    entry["tier"], entry["tier_reasons"] = extract.tier_of(entry, curated.get("tier"))

    # The excerpt panel follows the same rule as everything else, with one
    # boundary. A translated README the repository publishes itself is the same
    # document in the reader's language, and this site is read in English -- so
    # showing the Chinese one there served nobody, and the panel names the file
    # it came from so following the link holds no surprise. The English
    # *sections* of a bilingual README are a different matter: they are
    # fragments, and fragments presented as "the README" are worse than the
    # document, so those still show the original.
    excerpt_source = readme
    excerpt_name = None
    if sidecar and source is sidecar:
        excerpt_source, excerpt_name = sidecar, node.get("readmeEnglishName")

    detail = None
    if readme:
        detail = {
            "id": entry["id"],
            "headings": headings,
            "readme_excerpt": extract.clean_markdown(excerpt_source)[:4000],
            **({"readme_source": excerpt_name} if excerpt_name else {}),
        }
        entry["detail"] = f"detail/{entry['owner']}__{entry['repo']}.json"
    return entry, detail, extract.condense_readme(source)


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
    deepen_roots(client, found)
    return found


def deepen_roots(client, nodes):
    """Fetch the second level of the tree, for the few that need it.

    Most repositories answer the marker question at their root, and the search
    already has that. The rest keep the plugin one directory down, and folding
    that second level into the search query answered it for everyone at once --
    which sounds efficient and is not: GitHub walks every top-level directory
    server-side, and the search went from 4.1s a page to 9.6s. Over the fifty-odd
    pages a full build reads, four minutes to learn something about eighty
    repositories.

    Asked separately and in bulk it costs four requests. `has_plugin_marker`
    reads whichever shape it is handed -- an unexpanded directory means
    "nothing found here", never an error -- so nodes that keep their flat root
    are unaffected, and a repository that fails to come back keeps the answer
    the root already gave.
    """
    shallow = [key for key, node in nodes.items() if not has_plugin_marker(node)]
    if not shallow:
        return
    trees = client.fetch_trees(shallow)
    for key, root in trees.items():
        nodes[key]["root"] = root
    deeper = sum(1 for key in trees if has_plugin_marker(nodes[key]))
    print(f"  {len(shallow)} roots held no plugin; a second level found "
          f"{deeper} more")


# ----------------------------------------------------------------------- output

# The README carries the live numbers between these markers. That is worth
# reading on its own, and it is also the heartbeat: GitHub disables a
# scheduled workflow after 60 days without repository activity, and nothing
# else here ever commits -- the index is published as a Pages artifact, never
# checked in. A monthly rewrite of these four lines resets that clock with
# thirty days to spare, and unlike a dummy commit it says something true.
README_START = "<!-- index-status:start -->"
README_END = "<!-- index-status:end -->"

# Real newlines rather than escapes, so the block reads here the way it reads
# in the README.
STATUS_BLOCK = """{start}
> [!TIP]
> ### [Search the index &nbsp;&rarr;]({url}/)
> **{plugins} plugins &middot; {patches} patches** across {repos} repositories.
> Last full rebuild **{date}**, diffed nightly.
"""


def update_readme_status(counts, date, url=PAGES_BASE):
    """Rewrite the status block in README.md. Full builds only.

    Returns True when the file changed. Missing markers are not an error:
    somebody may have taken the block out on purpose, and a rebuild is the
    wrong place to put it back.
    """
    path = ROOT / "README.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    head, sep, rest = text.partition(README_START)
    if not sep:
        print("  README status markers missing, left alone")
        return False
    _, sep, tail = rest.partition(README_END)
    if not sep:
        print("  README end marker missing, left alone")
        return False

    block = STATUS_BLOCK.format(
        start=README_START,
        url=url,
        plugins=counts["plugins"],
        patches=counts["patches"],
        repos=counts["patch_repos"],
        date=date,
    )
    updated = head + block + README_END + tail
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    return path.stat().st_size


# Only what a device needs to search, show a row, and install. Everything a
# reader could tap through to on a bigger screen -- topics, licence, fork
# counts, tier reasoning, feature lists -- stays out, because this file is
# fetched over a patchy connection onto hardware with a slow processor.
MIN_FIELDS = ("owner", "repo", "categories", "tier", "activity", "stars")

# Keywords were more than half the file at 24 apiece. They are ordered by how
# identifying they are -- topics first, then description words, then README
# frequency terms -- so the tail is the noisy half and the first twelve carry
# the search. `id` is left out entirely: it is owner .. "/" .. repo.
MIN_KEYWORDS = 12


def minimal_entry(entry, purpose_chars=130):
    out = {field: entry.get(field) for field in MIN_FIELDS}
    out["keywords"] = (entry.get("keywords") or [])[:MIN_KEYWORDS]
    text = entry.get("purpose") or entry.get("description") or ""
    if len(text) > purpose_chars:
        text = text[:purpose_chars].rsplit(" ", 1)[0] + "…"
    out["purpose"] = text
    return out


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


def coverage_of(plugins):
    """How much of the catalogue an English query can actually reach.

    Every other number this build reports counts what was found. This one
    counts what was found and cannot be used, which is the failure mode the
    index had no way to see: an entry documented in another script scores zero
    against every query, and a search that quietly never returns it looks
    exactly like a search with no answer. 57 plugins sat in that state for
    months and nothing in the build said so.

    Published in index.json rather than only printed, so the trend is
    answerable later without re-deriving it -- the question "is this getting
    better or worse" could not be asked at all before, and a number that only
    ever appears in a log answers it no better than none.
    """
    counts = {"english": 0, "unreadable": 0, "silent": 0}
    for entry in plugins:
        counts[extract.readability(entry.get("purpose"),
                                   entry.get("description"))] += 1
    total = len(plugins) or 1
    return {
        "plugins": len(plugins),
        **counts,
        # Fewer than three keywords is not enough surface to be found by
        # anything but the plugin's own name.
        "thin_keywords": sum(1 for e in plugins if len(e.get("keywords") or []) < 3),
        "english_share": round(counts["english"] / total, 4),
    }


def report_coverage(coverage):
    total = coverage["plugins"] or 1
    print(f"  reachable in English: {coverage['english']} "
          f"({coverage['english'] / total:.1%})")
    print(f"  documented but unreadable: {coverage['unreadable']} "
          f"({coverage['unreadable'] / total:.1%})")
    print(f"  no prose at all: {coverage['silent']} "
          f"({coverage['silent'] / total:.1%})")
    print(f"  fewer than three keywords: {coverage['thin_keywords']}")


def sanity_check(plugins, patches, previous, previous_patches):
    """Refuse to publish a catalogue that collapsed.

    A bad hour at the GitHub API must not be able to empty the index; the last
    good publish is a better answer than a truncated one.

    Both halves are checked. When this guarded plugins alone, a bug that
    silently dropped every carried-over patch in diff mode would have shipped:
    the plugin count was untouched, so nothing objected.
    """
    if not previous:
        return True, ""
    checks = [
        ("plugin", len(previous.get("plugins", [])), len(plugins)),
        ("patch", len(previous_patches or []), len(patches)),
    ]
    for label, before, after in checks:
        if before and after < before * 0.7:
            return False, f"{label} count dropped {before} -> {after} (>30%)"
    return True, ""


# --------------------------------------------------------------- diff window

def diff_window(previous, since_days, max_days, now=None):
    """The date to hand GitHub as `pushed:>`, or None to rebuild in full.

    The floor is `since_days` back from today, which is all a diff used to
    have: wider than the daily cadence, so an ordinary late run still overlaps
    the one before it. What a fixed floor cannot survive is a run that does
    not happen at all. GitHub's scheduler is best-effort -- on 27 August 2026
    a run booked for 03:23 UTC started at 14:22, and the day before that the
    scheduler dropped runs outright -- and a night that is skipped leaves the
    repositories pushed inside the gap invisible to every run afterwards.
    Nothing reports that; the index simply goes quietly stale in places.

    So the window also reaches back to whatever the published index says it
    was built from. That is a better signal than the run history: a run can go
    green and still fail to publish, and a build can be red after the index is
    already live. `generated_at` describes what readers actually have, and it
    is already in hand -- a diff run downloads that index regardless.

    A day is subtracted from it because `pushed:>DATE` excludes DATE itself
    and the last build ran partway through its own day. Past `max_days` the
    diff stops being the cheap option, and a full run is owed anyway: it is
    the only one that sees deletions and removed topics.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=since_days)).date()
    note = f"{since_days}-day window"

    stamp = (previous or {}).get("generated_at")
    if stamp:
        try:
            built = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
        except ValueError:
            note += f"; generated_at {stamp!r} unreadable"
        else:
            reach = built - datetime.timedelta(days=1)
            if reach < since:
                since, note = reach, f"reaching back to the {built} build"

    gap = (now.date() - since).days
    if gap > max_days:
        return None, f"last index {gap} days back, past the {max_days}-day diff limit"
    return since.strftime("%Y-%m-%d"), note


# ------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "diff"], default="diff")
    parser.add_argument("--since-days", type=int, default=2,
                        help="diff window floor; wider than the daily cadence on purpose")
    parser.add_argument("--max-diff-days", type=int, default=14,
                        help="a gap wider than this is rebuilt in full instead")
    parser.add_argument("--out", default=str(DOCS))
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out)
    client = Client()
    curation = load_curation()
    started = now_iso()

    previous = fetch_url(PUBLISHED_INDEX)
    # Patches moved out of index.json in schema 2, so their previous state now
    # lives in its own file. Reading them from the old place returned nothing,
    # and a diff run would have republished the handful of repositories pushed
    # that day as the entire patch catalogue.
    previous_patches = (fetch_url(PATCHES_URL) or {}).get("patches", []) if previous else []
    if previous:
        print(f"previous index: {len(previous.get('plugins', []))} plugins, "
              f"{len(previous_patches)} patches ({previous.get('generated_at')})")
    elif args.mode == "diff":
        print("no previous index reachable, falling back to a full build")
        args.mode = "full"

    since = None
    if args.mode == "diff":
        since, why = diff_window(previous, args.since_days, args.max_diff_days)
        if since is None:
            print(f"{why}; falling back to a full build")
            args.mode = "full"
        else:
            print(f"diff mode: repositories pushed since {since} ({why})")
    if args.mode == "full":
        print("full mode: enumerating everything")

    print("collecting plugins…")
    nodes = collect_plugins(client, since)
    print(f"  {len(nodes)} repositories returned")
    attach_english_readmes(client, nodes)

    entries, details, readmes = {}, {}, {}

    # Deep search reuses whatever the previous run condensed, so a diff run
    # does not lose README text for the repositories it did not touch.
    # Detail files are carried the same way, and for a sharper reason: the
    # entries the diff did not touch keep their `detail` path from the previous
    # index, but the Pages artifact replaces the whole site. Writing only the
    # freshly built ones published a catalogue where every plugin untouched
    # that night answered its README link with a 404 -- which, between two
    # monthly full builds, is nearly all of them. One fetch restores the lot;
    # asking the published site for 750 individual files would not.
    if args.mode == "diff":
        previous_readmes = fetch_url(READMES_URL) or {}
        readmes.update(previous_readmes.get("readmes", {}))
        previous_details = fetch_url(DETAILS_URL)
        if previous_details:
            details.update(previous_details.get("details", {}))
        else:
            print("  no published details.json: README excerpts will be thin "
                  "until the next full build")

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
        else:
            # Rebuilt and found to have no README any more. Without this the
            # carried-over excerpt would outlive the document it came from.
            details.pop(entry["id"], None)
        if condensed:
            readmes[entry["id"]] = condensed

    plugins = sorted(entries.values(), key=lambda e: (-e["stars"], e["id"].lower()))

    coverage = coverage_of(plugins)
    report_coverage(coverage)

    print("collecting patches…")
    patch_entries, patch_repos, patch_repos_seen = collect_patches(client, curation, since)
    # A diff run only sees repositories pushed recently, so patches from
    # untouched repos are carried over the same way plugins are.
    if args.mode == "diff" and previous:
        have = {p["id"] for p in patch_entries}
        for old in previous_patches:
            if old["id"] not in have:
                patch_entries.append(old)
        patch_repos = max(patch_repos, previous.get("counts", {}).get("patch_repos", 0))
    patches = sorted(patch_entries, key=lambda e: (-e["repo_stars"], e["id"].lower()))
    print(f"  {len(patches)} patch files across {patch_repos} repos "
          f"({patch_repos_seen} matched the search)")

    ok, why = sanity_check(plugins, patches, previous, previous_patches)
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
        "coverage": coverage,
        # Plugins only. Counts are per tab, and a chip on the patches tab
        # claiming 151 interface entries when it can show none of the
        # plugin ones is worse than showing no count at all.
        "categories": category_summary(plugins),
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
        "categories": category_summary(patches),
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

    # The third consumer: a document for assistants, and a pointer file for
    # crawlers. Both are generated, so neither can go stale the way the
    # hand-written knowledge base did.
    kb = knowledge_base.render(index, patches, curation["distinctions"], PAGES_BASE + "/")
    (out_dir / "knowledge-base.md").write_text(kb, encoding="utf-8")
    (out_dir / "llms.txt").write_text(
        knowledge_base.render_llms_txt(index, patches, PAGES_BASE, SOURCE_REPO, APPSTORE_URL),
        encoding="utf-8")
    kb_size = len(kb.encode("utf-8"))

    # The fourth consumer: anything that reads the web without running the
    # page's JavaScript. The search page renders every result in script, so the
    # HTML a crawler downloads says "Loading…" and nothing else; this is the
    # same catalogue as flat HTML, plus the two files that tell a crawler what
    # to fetch and what to leave alone.
    catalogue = seo.render_catalogue(index, patches, PAGES_BASE, SOURCE_REPO, APPSTORE_URL)
    (out_dir / "catalogue.html").write_text(catalogue, encoding="utf-8")
    (out_dir / "robots.txt").write_text(seo.render_robots(PAGES_BASE), encoding="utf-8")
    (out_dir / "sitemap.xml").write_text(seo.render_sitemap(PAGES_BASE, started), encoding="utf-8")
    catalogue_size = len(catalogue.encode("utf-8"))

    # Plugins only, and tier C left out: on e-ink the long tail is unreachable
    # noise, and it is a third of the file.
    device_entries = [minimal_entry(e) for e in plugins if e["tier"] != "C"]
    min_size = write_json(out_dir / "index.min.json", {
        "schema": SCHEMA_VERSION,
        "generated_at": started,
        "counts": {"plugins": len(device_entries)},
        "categories": category_summary(plugins),
        "plugins": device_entries,
    })

    # Per file for the page, which fetches one when a README is expanded, and
    # once as a whole for the next diff run, which cannot fetch 750 of them.
    live_details = {pid: d for pid, d in details.items() if pid in entries}
    for detail in live_details.values():
        owner, repo = detail["id"].split("/", 1)
        write_json(out_dir / "detail" / f"{owner}__{repo}.json", detail)
    details_size = write_json(out_dir / "details.json", {
        "schema": SCHEMA_VERSION,
        "generated_at": started,
        "details": live_details,
    })

    tiers = {}
    for entry in plugins:
        tiers[entry["tier"]] = tiers.get(entry["tier"], 0) + 1
    misc = sum(1 for e in plugins if e["categories"] == ["misc"])

    print(f"\nwrote {out_dir/'index.json'} ({size/1024:.0f} KB)")
    print(f"  plugins   {len(plugins)}")
    print(f"  tiers     " + "  ".join(f"{k}:{v}" for k, v in sorted(tiers.items())))
    print(f"  misc-only {misc} ({misc*100//max(len(plugins),1)}%)")
    print(f"  details   {len(live_details)} ({details_size/1024:.0f} KB carried as details.json)")
    ptiers = {}
    for entry in patches:
        ptiers[entry["tier"]] = ptiers.get(entry["tier"], 0) + 1
    no_header = sum(1 for e in patches if not e["purpose"])
    print(f"  patches   {len(patches)} in {patch_repos} repos ({patch_size/1024:.0f} KB)")
    print(f"    tiers   " + "  ".join(f"{k}:{v}" for k, v in sorted(ptiers.items())))
    print(f"    no doc  {no_header}")
    print(f"  knowledge-base.md ({kb_size/1024:.0f} KB)  llms.txt")
    print(f"  catalogue.html    ({catalogue_size/1024:.0f} KB)  robots.txt  sitemap.xml")
    print(f"  index.min.json    ({min_size/1024:.0f} KB, {len(device_entries)} entries)")
    print(f"  requests  {client.requests}  (rate limit left: {client.remaining})")

    # Only on full runs: a nightly rewrite would be 365 commits a year saying
    # the same thing, and the diff numbers are carried over rather than
    # measured, so they are not the ones worth publishing.
    if args.mode == "full" and update_readme_status(index["counts"], started[:10]):
        print("  README.md status block updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
