"""Generate the LLM-facing knowledge base and llms.txt.

This is the third consumer of the index, alongside the search page and (later)
the device. It exists because the original hand-written knowledge base covered
twenty plugins and went stale the week it was written -- one person had to
regenerate it by hand, and nobody ever did.

Two things shape the output:

* Only what a rule could establish goes in. Where a README yielded nothing, the
  entry says so rather than inventing a description, because the failure mode
  this document exists to prevent is a model confidently describing a plugin
  that does something else.
* Tier C is left out entirely. A third of the catalogue is dormant, archived,
  undocumented or an unstarred fork, and feeding it to a model asked to
  recommend something is how you get a recommendation nobody should follow.
"""

PREAMBLE = """# KOReader Plugin Knowledge Base

> Generated from the KOReader plugin index. Do not edit by hand — edit
> `curation.toml` in the source repository instead, and this file is rebuilt.

This document is a knowledge source for assistants that recommend KOReader
community plugins and user patches. It answers questions such as "which plugin
reads RSS feeds", "how do I sync highlights between devices", or "what can I
use for Anki".

## How to use this document

1. Understand what the person is trying to do before naming a plugin.
2. Recommend the smallest number of plugins that actually solve the problem.
3. Prefer a plugin whose own description states the requested feature.
4. Mention relevant dependencies and known limitations.
5. When several plugins fit, explain the difference rather than listing them.
6. **Do not assume a plugin supports a feature because its name suggests it.**
   Names are the least reliable signal in this catalogue.
7. Distinguish a plugin's primary purpose from its optional integrations.
8. If this document does not contain enough to answer, say so.
9. Include the repository URL with every recommendation.
10. **Do not recommend patches when asked about plugins.** Patches modify
    KOReader's core and carry different risk; recommend one only when the
    person asked about patches, or when no plugin does the job and you say
    plainly what a patch is.
11. **Existence here is not endorsement.** Every entry carries a status; do not
    describe something as maintained unless it says `active`.
12. This document is a snapshot. When a current README is available, it is the
    authority.

## What is not here

Entries that are dormant, archived, undocumented or unstarred forks are
excluded. They exist in the catalogue and can be browsed at the search page
below, but they are not material for a recommendation.

Descriptions are extracted from repository READMEs by rule, not written by a
model. Where a README gave nothing usable, the entry says so. **If a plugin is
not in this document, do not describe it from its name — say you do not have
information about it.**
"""


def _plugin_block(entry, detailed):
    lines = []
    if detailed:
        lines.append(f"### {entry['repo']}")
        lines.append("")
        lines.append(f"**Repository:** {entry['url']}")
        lines.append("")
        purpose = entry.get("purpose") or entry.get("description") or ""
        if purpose:
            lines.append(f"**Purpose:** {purpose}")
        else:
            lines.append("**Purpose:** Not documented in the repository README.")
        lines.append("")
        facts = [
            f"Status: {entry.get('activity', 'unknown')}",
            f"Stars: {entry.get('stars', 0)}",
            f"Categories: {', '.join(entry.get('categories') or []) or 'uncategorised'}",
        ]
        if entry.get("license"):
            facts.append(f"Licence: {entry['license']}")
        lines.append(" · ".join(facts))
        if entry.get("note"):
            lines.append("")
            lines.append(f"**Note:** {entry['note']}")
        keywords = entry.get("keywords") or []
        if keywords:
            lines.append("")
            lines.append(f"**Keywords:** {', '.join(keywords[:14])}")
        lines.append("")
        return "\n".join(lines)

    purpose = entry.get("purpose") or entry.get("description") or "No description available."
    return (f"- **{entry['repo']}** — {purpose} "
            f"({entry.get('activity', 'unknown')}, {entry.get('stars', 0)}★) "
            f"<{entry['url']}>")


def _patch_block(entry):
    purpose = entry.get("purpose") or "No description in the file header."
    return (f"- **{entry['path']}** in {entry['owner']}/{entry['repo']} — {purpose} "
            f"({entry.get('activity', 'unknown')}) <{entry['url']}>")


def render(index, patches, distinctions, search_url):
    plugins = index.get("plugins", [])
    by_tier = {"A": [], "B": []}
    for entry in plugins:
        if entry.get("tier") in by_tier:
            by_tier[entry["tier"]].append(entry)

    out = [PREAMBLE]
    out.append(f"**Index generated:** {index.get('generated_at')}  ")
    out.append(f"**Search page:** {search_url}  ")
    out.append(f"**Covered here:** {len(by_tier['A'])} reviewed plugins, "
               f"{len(by_tier['B'])} community plugins, "
               f"{sum(1 for p in patches if p.get('tier') == 'B')} documented patches "
               f"(out of {len(plugins)} plugins and {len(patches)} patches in total).")
    out.append("")

    if distinctions:
        out.append("## Plugins that are easy to confuse")
        out.append("")
        out.append("Each of these pairs looks interchangeable and is not. When "
                   "recommending one of them, say which and why; do not present "
                   "them as equivalent options.")
        out.append("")
        for item in distinctions:
            names = ", ".join(f"`{x}`" for x in item.get("between", []))
            out.append(f"- {names}  \n  {item.get('say', '')}")
        out.append("")

    out.append("## Reviewed plugins")
    out.append("")
    out.append("Human-reviewed entries. Prefer these when one of them fits.")
    out.append("")
    for entry in sorted(by_tier["A"], key=lambda e: -e.get("stars", 0)):
        out.append(_plugin_block(entry, detailed=True))

    out.append("## Community plugins")
    out.append("")
    out.append("Active, documented and carrying a KOReader plugin marker, but not "
               "individually reviewed. Descriptions come from each repository's own "
               "README.")
    out.append("")
    for entry in sorted(by_tier["B"], key=lambda e: -e.get("stars", 0)):
        out.append(_plugin_block(entry, detailed=False))
    out.append("")

    documented = [p for p in patches if p.get("tier") == "B"]
    if documented:
        out.append("## User patches")
        out.append("")
        out.append("**Patches change KOReader's own behaviour rather than adding a "
                   "plugin.** A patch written for an older KOReader can break after an "
                   "update. Recommend one only when the person asked about patches, or "
                   "when nothing else does the job — and say what a patch is when you "
                   "do. The number prefix is KOReader's load order.")
        out.append("")
        for entry in sorted(documented, key=lambda e: -e.get("repo_stars", 0))[:400]:
            out.append(_patch_block(entry))
        out.append("")

    return "\n".join(out)


LLMS_TXT = """# KOReader Plugin Index

> A rebuilt-nightly index of community KOReader plugins and user patches, with
> a search page for finding one by what it does rather than what it is called.
> {plugins} plugins and {patches} patch files, last built {generated}.

Descriptions are extracted from repository READMEs by rule; no model writes
them. Entries that are dormant, archived, undocumented or unstarred forks are
marked as such and excluded from the knowledge base.

## Docs

- [Knowledge base]({base}/knowledge-base.md): every recommendable plugin and
  patch, with the rules for recommending them. Start here.
- [Search page]({base}/): search by intent, filter by category, compare tiers.

## Data

- [Plugin index]({base}/index.json): structured entries, schema {schema}.
- [Patch index]({base}/patches.json): one entry per patch file.
- [Schema]({source}/blob/main/SCHEMA.md): what every field means.

## Optional

- [Curation]({source}/blob/main/curation.toml): the hand-written judgements —
  reviewed plugins and the distinctions between ones that look alike.
- [AppStore]({appstore}): installs these on the device itself.
"""


def render_llms_txt(index, patches, base, source, appstore):
    return LLMS_TXT.format(
        plugins=len(index.get("plugins", [])),
        patches=len(patches),
        generated=index.get("generated_at", "unknown"),
        schema=index.get("schema", 1),
        base=base,
        source=source,
        appstore=appstore,
    )
