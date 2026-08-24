# `index.json` schema (v2)

The published index is the contract between one producer and several consumers.
Changing it later is the most expensive edit in this project, so it is versioned
from day one via the top-level `schema` field.

```
producer                       consumers
─────────                      ─────────
scripts/build.py  ──►  index.json  ──►  docs/index.html      (search page)
                                   ──►  appstore.koplugin    (device, later)
                                   ──►  knowledge-base.md    (LLM / custom GPT)
```

## Published files

| File | Consumer | Notes |
| --- | --- | --- |
| `docs/index.json` | search page | Plugins only. Full entries, no long prose. |
| `docs/patches.json` | search page, on tab open | Patch entries. Split out in schema 2 — inlined, index.json passed a megabyte. |
| `docs/detail/<owner>__<repo>.json` | search page, on demand | README-derived long text. |
| `docs/details.json` | the next build | Every detail document in one file. A diff run carries these over; it cannot fetch 750 separate files. |
| `docs/readme-index.json` | search page, opt-in | Condensed README text per plugin, for deep search. ~1 MB, lazily loaded. |
| `docs/index.min.json` | device (Lua) | Reserved for phase 6. Same entry shape, fewer fields. |
| `docs/knowledge-base.md` | LLM | Generated prose, not consumed by code. |
| `docs/llms.txt` | crawlers | Pointer file. |

Consumers must ignore unknown fields, so additive changes never require a
version bump. `schema` increments only on a removal or a meaning change.

## Top level

```json
{
  "schema": 1,
  "generated_at": "2026-08-15T03:07:41Z",
  "source_repo": "https://github.com/omer-faruq/koreader-plugin-index",
  "counts": { "plugins": 739, "patches": 612, "patch_repos": 122 },
  "coverage": { "plugins": 752, "english": 696, "unreadable": 38, "silent": 18,
                "thin_keywords": 34, "english_share": 0.9255 },
  "categories": [
    { "id": "sync", "label": "Sync", "count": 24 }
  ],
  "plugins": [],
  "patches_url": "patches.json"
}
```

`coverage` counts what the index holds but cannot be searched for. Ranking
tokenises on `[a-z0-9]+`, so an entry whose only prose is in another script
scores zero against every query — a failure that looks, from the outside,
exactly like a search with no answer. `english` is reachable, `unreadable` is
documented in a script the scorer cannot read, `silent` has no prose at all.
The quality suite refuses to publish a run where these collapse.

`generated_at` is what the page shows as *"Index: 15 Aug 2026"*. Consumers must
display it — the index is deliberately stale between weekly runs, and saying so
turns a discrepancy against the live GitHub catalogue into transparency rather
than a bug.

`counts` covers both files, so a consumer can show totals without fetching
patches. `patches_url` points at the separate file; follow it only when patches
are actually needed.

Plugins and patches must never be concatenated into one result list. A patch monkey-patches KOReader core; a plugin does not. They
carry different risk and are recommended under different rules.

## Plugin entry

The unit is a **repository**.

```json
{
  "id": "omer-faruq/webbrowser.koplugin",
  "owner": "omer-faruq",
  "repo": "webbrowser.koplugin",
  "url": "https://github.com/omer-faruq/webbrowser.koplugin",
  "description": "Text-oriented web browser for KOReader",
  "purpose": "Browse and read web pages from KOReader on e-ink devices.",
  "categories": ["web"],
  "keywords": ["web", "browser", "search", "articles", "jina"],
  "topics": ["koreader-plugin", "eink"],

  "tier": "A",
  "tier_reasons": ["curated", "has_meta", "active", "documented"],
  "activity": "active",

  "stars": 143,
  "forks": 7,
  "is_fork": false,
  "archived": false,
  "pushed_at": "2026-07-30T11:02:18Z",
  "created_at": "2025-11-04T09:12:41Z",
  "license": "MIT",
  "default_branch": "main",

  "has_meta": true,
  "readme_bytes": 8213,
  "detail": "detail/omer-faruq__webbrowser.koplugin.json"
}
```

| Field | Purpose |
| --- | --- |
| `id` | Stable key, `owner/repo`. Used by `curation.yaml` and by the test set. |
| `owner`, `repo` | Kept split so the page can build the AppStore handoff link (`index.html?tab=plugins&owner=…&q=…`) without parsing `id`. |
| `description` | GitHub's own one-liner, verbatim. |
| `purpose` | First meaningful README paragraph, trimmed. Empty string when the README gives nothing usable — never invented. Read from the English view of the README rather than the file itself: a `README_en.md` beside it, or the English sections of a bilingual document. Where that leaves a Chinese sentence and GitHub's own description is in English, the description is used instead — every consumer reads `purpose or description` and stops at the first, so the better line would otherwise never be seen. |
| `keywords` | Match surface for search. Union of topics, description words, and README headings, after stop-word removal. For a plugin documented in another script, the `[glossary]` table in `curation.toml` adds English labels for the concepts its opening prose and headings name — labels, never prose, and never a `purpose`. |
| `has_meta` | `_meta.lua` present in the repo root. The single strongest "this is a real KOReader plugin" signal. |
| `detail` | Relative path, or `null` when there is nothing beyond what is inlined here. |

## Patch entry

The unit is a **file**, not a repository. 122 patch repos hold roughly 600
patch files; a repo with 14 patches is one row in the AppStore's repo list but
fourteen separate things a user might want.

```json
{
  "id": "omer-faruq/koreader-user-patches:2-fontsize.lua",
  "owner": "omer-faruq",
  "repo": "koreader-user-patches",
  "path": "2-fontsize.lua",
  "order": 2,
  "url": "https://github.com/omer-faruq/koreader-user-patches/blob/main/2-fontsize.lua",
  "raw_url": "https://raw.githubusercontent.com/omer-faruq/koreader-user-patches/main/2-fontsize.lua",

  "purpose": "Allow font sizes below the built-in minimum.",
  "categories": ["ui"],
  "keywords": ["font", "size", "minimum"],

  "tier": "B",
  "tier_reasons": ["has_header", "active"],
  "activity": "active",

  "file_sha": "9f1c…",
  "file_modified_at": "2026-05-02T08:14:55Z",
  "file_bytes": 1204,
  "repo_stars": 118,
  "repo_pushed_at": "2026-08-11T19:33:07Z"
}
```

Two fields carry the weight here:

- **`order`** is the `N-` filename prefix, which is KOReader's patch load order.
  It is semantic, not decoration, and must survive into the index.
- **`file_modified_at`** is the last change to *this file*, not to the
  repository. A repo with fourteen patches looks active while any one of them
  is two years dead. For plugins staleness costs a feature; for patches it can
  cost a boot, so patch freshness is measured per file.

`purpose` comes from the leading comment block of the `.lua` file. KOReader
patch convention is to explain the patch at the top of the file, which makes
this a reliable deterministic target. The repo README is a weak fallback only:
one README describes all fourteen patches at once.

## Tiers

The catalogue is 739 plugins and growing, and writing a KOReader plugin got
much easier — so a growing share is abandoned experiments, forks and generated
code that never ran. Past a certain size the value of this project is not
listing things, it is **excluding** them.

Every tier signal is deterministic and free. No model is involved.

| Tier | Meaning | Shown by default |
| --- | --- | --- |
| `A` | Listed in `curation.yaml` — human-reviewed | yes |
| `B` | `has_meta`, not a fork, not archived, active, README has substance | yes |
| `C` | Everything else: dormant, archived, a stub, or a fork nobody starred | on request, badged |

Being a fork is **not** on its own a demotion. In this ecosystem plugins
routinely begin as a fork of a template or of another plugin, and three of the
most-starred entries in the catalogue are forks. Only an *unstarred* fork is
treated as noise — the same line the AppStore page already draws with its
"include zero-star forks" toggle. Note also that GitHub search omits forks
unless the query says `fork:true`, so discovery must ask for them explicitly.

`tier_reasons` is an array of the signals that produced the tier, so the page
can render *why* something is demoted rather than silently hiding it:

`curated`, `has_meta`, `no_meta`, `active`, `dormant`, `archived`, `fork`,
`documented`, `stub`

`created_at` is the repository's, not the plugin's: a plugin that began life
inside another repository dates from the move. It is absent on entries a diff
run carried over from before the field existed, so consumers sort on it only
where present.

`activity` is derived from `pushed_at` (or `file_modified_at` for patches):
`active` under 12 months, `dormant` beyond it, `archived` when GitHub says so.

Consumers must not treat existence as endorsement. This is rule 11 of the
knowledge base, promoted from prose the model is asked to remember into a field
it cannot miss.

## Categories

A fixed taxonomy, because free-form topics do not group 739 repositories into
anything a person can scan — and on e-ink, tapping a category is the only
usable alternative to typing a sentence.

| id | Label | Covers |
| --- | --- | --- |
| `sync` | Sync | Highlights, annotations, progress, cloud storage |
| `ui` | Interface | Themes, home screen, menus, overlays |
| `dict` | Dictionary & language | Dictionaries, translation, Anki, vocabulary |
| `web` | Web & articles | Browser, RSS, read-later, article capture |
| `library` | Library & catalogue | OPDS, Calibre, Zotero, book sources |
| `ai` | AI | Assistants, AI translation and summaries |
| `device` | Device specific | Kobo, Kindle, Android, stylus |
| `files` | Files & transfer | Wireless transfer, file management, backup |
| `reading` | Reading experience | Statistics, progress, typography, layout |
| `content` | External content | Manga, web novels, remote libraries |
| `notes` | Notes & annotation | Note-taking, handwriting, annotation viewers |
| `games` | Games & puzzles | Sudoku, solitaire, word puzzles |
| `misc` | Other | Nothing above fits |

`device` is matched against the repository name and topics only, never the
description. "Works on Kobo and Kindle" appears in a quarter of all
descriptions, which made it the largest bucket in the catalogue and the chip
useless; in a name or a topic a device is a claim of identity instead.

Widening this vocabulary against the published index took `misc` from 33% of
the catalogue to 19%.

Assignment is rule-based: `curation.yaml` first, then topic and keyword
matching. An entry may hold more than one category; `misc` is only used when
nothing matched, and a large `misc` bucket is a signal that the rules need
work, not that the taxonomy does.

## `curation.yaml`

The only hand-written file in the repository, and the reason regeneration is
safe. The generator **merges** it over generated fields; it never lets a weekly
rebuild overwrite a human judgement.

```yaml
plugins:
  omer-faruq/webbrowser.koplugin:
    tier: A
    categories: [web]
    purpose: "Browse and read web pages from KOReader on e-ink devices."

  OctoNezd/zlibrary.koplugin:
    tier: A
    note: >
      Describes the repository's stated functionality. Availability and
      legality vary by jurisdiction; do not advise on either.

distinctions:
  - between: [gitalexcampos/highlightsync.koplugin, dani84bs/AnnotationSync.koplugin]
    say: >
      Both synchronise annotations. They are not interchangeable — the choice
      depends on the sync service you already use.

  - between: [omer-faruq/assistant.koplugin, zeeyado/koassistant.koplugin]
    say: >
      Related projects, different repositories. Do not treat them as the same
      plugin.
```

What lives here is exactly what a README cannot yield: the distinctions, the
cautions, the "these two look alike but are not" notes. Those are the most
valuable part of the existing hand-written knowledge base and the part that
would be destroyed by a naive regeneration.

It is also small enough for the community to send pull requests against, which
is how curation stops depending on one person.

## Quality tests

`tests/queries.yaml` holds query → expected-result pairs, seeded from the
"Recommendation Examples" section of the original knowledge base:

```yaml
- query: "I want to sync my highlights between devices"
  expect_top3: [gitalexcampos/highlightsync.koplugin]
- query: "Anki integration"
  expect_top3: [Ajatt-Tools/anki.koplugin]
- query: "read websites on my e-reader"
  expect_top3: [omer-faruq/webbrowser.koplugin]
```

The build runs these after generating the index and fails loudly when a known
answer falls out of the top three. Ranking is then tuned against measurements
instead of intuition, and quality degrading over time becomes visible instead
of silent.
