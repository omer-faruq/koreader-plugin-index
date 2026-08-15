# koreader-plugin-index

A self-maintaining index of community KOReader plugins and user patches, and a
search page built on it.

The AppStore plugin already discovers what exists. This project answers the
question it cannot: **which of these do I actually want?**

## Why it exists

The KOReader plugin catalogue passed 739 repositories in August 2026, plus 122
patch repositories holding roughly 600 individual patch files. Writing a plugin
got much easier, so that number grows — and a growing share of it is abandoned
experiments, forks and generated code that never ran.

Past a certain size the useful work is not listing things. It is excluding
them, and explaining the difference between the ones that remain.

The existing knowledge base did that by hand for twenty plugins. It went stale
the week it was written, because one person had to regenerate it. This project
keeps the facts fresh automatically and keeps the human judgement in one small
file that a rebuild never overwrites.

## How it works

```
GitHub  (739 plugin repos · 122 patch repos)
   │  daily diff · monthly full rebuild
   ▼
scripts/build.py  ──  curation.toml  (hand-written judgement, merged on top)
   │
   ▼
docs/index.json  ──►  search page      (this repo's GitHub Pages)
                 ──►  AppStore plugin  (on device, later)
                 ──►  knowledge-base.md for LLM assistants
```

No model runs in the pipeline. Every field is extracted by rule, which is what
keeps rebuilds free, reproducible, and free of an API key that would eventually
expire. The reasoning happens on the consumer's side, with their own tools.

Running cost is zero: Actions and Pages are free for public repositories, and
`GITHUB_TOKEN` is provided by the workflow and never needs rotating.

## Layout

| Path | What it is |
| --- | --- |
| `scripts/build.py` | Builds the index. `--mode diff` (daily) or `full` (monthly). |
| `scripts/github.py` | GitHub client. Standard library only. |
| `scripts/extract.py` | README → purpose, keywords, categories, tier. |
| `scripts/rank.py` | Reference ranking. The search page mirrors these rules. |
| `curation.toml` | **The only hand-written file.** |
| `tests/queries.toml` | Known question/answer pairs; the build fails if ranking regresses. |
| `SCHEMA.md` | The `index.json` contract. |

## Curation

`curation.toml` holds what a README cannot yield: which plugins are worth
recommending, and which ones look interchangeable but are not.

```toml
[plugins."omer-faruq/webbrowser.koplugin"]
tier = "A"
categories = ["web"]

[[distinctions]]
between = ["gitalexcampos/highlightsync.koplugin", "dani84bs/AnnotationSync.koplugin"]
say = "Both synchronise annotations, and they are not interchangeable."
```

The generator merges this over everything it extracted and never overwrites it.
Pull requests are welcome — curation should not depend on one person, and this
file is small enough to review.

## Running it locally

Nothing to install; Python 3.11+ is the only requirement. GitHub's GraphQL API
requires authentication even for public data, so a token is needed:

```sh
export GITHUB_TOKEN=ghp_...          # classic token, public_repo scope
python scripts/build.py --mode full
python scripts/test_queries.py
```

In Actions no token needs to be created: the workflow's own `GITHUB_TOKEN`
covers it.

## Status

- [x] Schema, generator, tiers, categories, ranking, quality tests
- [x] Search page (`docs/index.html`), with a parity check against `rank.py`
- [ ] First live run against the GitHub API
- [ ] Patches — the unit there is a file, not a repository
- [ ] `knowledge-base.md` and `llms.txt` for LLM assistants
- [ ] On-device search in the AppStore plugin
