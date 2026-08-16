"""Crawlable outputs: robots.txt, sitemap.xml and the static catalogue page.

The search page is one HTML file that fetches index.json and builds every
result in JavaScript. That is the right shape for a reader -- one request, no
framework, works offline once cached -- but it means the HTML a crawler
downloads contains a heading, a search box and the word "Loading…". Google
renders JavaScript and eventually sees the real list; Bing, DuckDuckGo, social
previews and the assistants that read the open web mostly do not.

So the catalogue is also written out as plain HTML that needs nothing to read
it. It is generated from the same index as everything else, which is the whole
point: a hand-written page listing plugins would go stale the week it was
written, exactly like the knowledge base this project replaced.

Only tier A and B go in, for the same reason they are the only tiers in the
knowledge base -- a third of the catalogue is dormant, archived, undocumented
or an unstarred fork, and publishing that as indexable content is asking search
engines to rank abandoned experiments alongside maintained plugins.
"""

import html
import json

# Patches are files inside somebody else's repository rather than projects of
# their own, and there are hundreds. The tail is one-line tweaks whose header
# says nothing; listing all of it would bury the plugins that carry the page.
PATCH_LIMIT = 200

CATALOGUE_CSS = """
:root {
  --bg: #f7f7f5; --surface: #ffffff; --border: #dcdad4;
  --text: #1a1a18; --muted: #6b6a64; --accent: #2f5d50; --accent-soft: #e4efe9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --surface: #1e2024; --border: #34373d;
    --text: #e8e8e6; --muted: #9b9c9e; --accent: #7fc0aa; --accent-soft: #22322c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 860px; margin: 0 auto; padding: 24px 20px 72px; }
a { color: var(--accent); }
h1 { font-size: 1.55rem; margin: 0 0 4px; letter-spacing: -.02em; }
/* The offset clears the sticky bar. Without it a jump from the contents list
   lands the heading underneath it, which reads as the wrong section. */
h2 {
  font-size: 1.15rem; margin: 34px 0 4px; padding-top: 14px;
  border-top: 1px solid var(--border); scroll-margin-top: 78px;
}
.count { color: var(--muted); font-weight: 400; font-size: .85rem; }
h3 { font-size: 1rem; margin: 0 0 2px; }
.tagline { color: var(--muted); margin: 0 0 14px; }
.lead {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 16px; margin: 0 0 20px;
}
.lead p { margin: 0 0 8px; }
.lead p:last-child { margin: 0; }
.toc { display: flex; flex-wrap: wrap; gap: 6px 12px; padding: 0; margin: 12px 0 0; list-style: none; font-size: .9rem; }

/* Somebody who arrives from a search engine lands in the middle of a list of
   several hundred entries, and the sentence at the top offering the finder has
   long since scrolled away. This is that offer, kept within reach.
   No script: a GET form navigates to ./?q=… and the finder reads the query
   string on load, which is also what makes the page shareable. A page that
   exists to work without JavaScript should not grow a search box that needs
   it. */
.jump {
  position: sticky; top: 0; z-index: 5;
  display: flex; gap: 8px; align-items: center;
  margin: 18px 0 0; padding: 10px 0;
  background: var(--bg); border-bottom: 1px solid var(--border);
}
.jump form { display: flex; gap: 8px; flex: 1; min-width: 0; }
.jump input {
  flex: 1; min-width: 0; padding: 9px 14px; font: inherit; font-size: .92rem;
  color: var(--text); background: var(--surface);
  border: 1px solid var(--border); border-radius: 999px;
}
.jump input:focus { outline: none; border-color: var(--accent); }
.jump button, .jump .ai {
  border: 1px solid var(--border); background: var(--surface); color: var(--accent);
  border-radius: 999px; padding: 9px 16px; font: inherit; font-size: .88rem;
  font-weight: 600; cursor: pointer; text-decoration: none; white-space: nowrap;
}
.jump button:hover, .jump .ai:hover { border-color: var(--accent); background: var(--accent-soft); }
.jump .ai { display: inline-flex; align-items: center; gap: 6px; }
.jump .ai svg { width: 15px; height: 15px; fill: currentColor; }
@media (max-width: 560px) { .jump .ai span { display: none; } }

.entry { padding: 12px 0; border-bottom: 1px solid var(--border); scroll-margin-top: 78px; }
.entry:last-child { border-bottom: 0; }
.entry p { margin: 0; }
.meta { color: var(--muted); font-size: .82rem; margin: 0 0 6px !important; }
.note { font-size: .88rem; color: var(--muted); margin-top: 6px !important; }
.undocumented { color: var(--muted); font-style: italic; }
.patches { padding-left: 20px; }
.patches li { margin-bottom: 8px; }
.patches .meta { display: block; }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--border); color: var(--muted); font-size: .85rem; }
"""


def _esc(text):
    return html.escape(str(text or ""), quote=True)


def _anchor(entry_id):
    return "e-" + "".join(c if c.isalnum() else "-" for c in entry_id)


def _label_map(index):
    return {c["id"]: c["label"] for c in index.get("categories", [])}


def _purpose(entry):
    text = entry.get("purpose") or entry.get("description") or ""
    if text:
        return f"<p>{_esc(text)}</p>"
    # Saying so beats inventing a sentence. The extraction rules produce
    # nothing for a repository whose README is a screenshot and a title, and
    # a plausible-sounding guess is exactly what this project exists to avoid.
    return '<p class="undocumented">No description in the repository README.</p>'


def _entry_html(entry, labels):
    facts = [
        f"{_esc(entry['owner'])}/{_esc(entry['repo'])}",
        f"★ {entry.get('stars', 0)}",
        _esc(entry.get("activity", "unknown")),
    ]
    cats = [labels.get(c, c) for c in entry.get("categories") or []]
    if cats:
        facts.append(_esc(", ".join(cats)))
    if entry.get("license"):
        facts.append(_esc(entry["license"]))

    note = f'<p class="note">{_esc(entry["note"])}</p>' if entry.get("note") else ""
    return (
        f'<article class="entry" id="{_anchor(entry["id"])}">\n'
        f'  <h3><a href="{_esc(entry["url"])}">{_esc(entry["repo"])}</a></h3>\n'
        f'  <p class="meta">{" · ".join(facts)}</p>\n'
        f'  {_purpose(entry)}{note}\n'
        f'</article>'
    )


def _patch_html(entry, labels):
    purpose = entry.get("purpose")
    body = (f"<br>{_esc(purpose)}" if purpose
            else '<br><span class="undocumented">No description in the file header.</span>')
    cats = ", ".join(labels.get(c, c) for c in entry.get("categories") or [])
    facts = [f"{_esc(entry['owner'])}/{_esc(entry['repo'])}", f"★ {entry.get('repo_stars', 0)}"]
    if cats:
        facts.append(_esc(cats))
    return (f'<li id="{_anchor(entry["id"])}">'
            f'<a href="{_esc(entry["url"])}"><code>{_esc(entry["path"])}</code></a> '
            f'<span class="meta">{" · ".join(facts)}</span>{body}</li>')


def _json_ld(index, patches, base, source):
    """Structured data for the catalogue page.

    Two things are being described and they are not the same: a page a person
    reads, and a dataset a machine downloads. Declaring the second is what puts
    index.json in front of anything looking for structured data rather than
    prose, and it carries the licence and the rebuild date with it.
    """
    generated = index.get("generated_at")
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "CollectionPage",
                "@id": f"{base}/catalogue.html",
                "url": f"{base}/catalogue.html",
                "name": "KOReader plugin catalogue",
                "description": (
                    f"Every recommendable KOReader plugin and user patch in the index: "
                    f"{index['counts']['plugins']} plugins and {index['counts']['patches']} "
                    f"patch files, rebuilt nightly from GitHub."
                ),
                "inLanguage": "en",
                "dateModified": generated,
                "isPartOf": {"@type": "WebSite", "@id": f"{base}/#website"},
            },
            {
                "@type": "Dataset",
                "@id": f"{base}/index.json",
                "name": "KOReader plugin index",
                "description": (
                    "Structured index of community KOReader plugins and user patches. "
                    "Descriptions, keywords, categories and activity are extracted from "
                    "repository READMEs by rule; no model writes them."
                ),
                "url": f"{base}/",
                "dateModified": generated,
                "creator": {"@type": "Organization", "name": "koreader-plugin-index", "url": source},
                "isAccessibleForFree": True,
                "keywords": ["KOReader", "e-reader", "plugins", "user patches", "Kobo", "Kindle"],
                "distribution": [
                    {"@type": "DataDownload", "encodingFormat": "application/json",
                     "contentUrl": f"{base}/index.json", "name": "Plugin index"},
                    {"@type": "DataDownload", "encodingFormat": "application/json",
                     "contentUrl": f"{base}/patches.json", "name": "Patch index"},
                    {"@type": "DataDownload", "encodingFormat": "text/markdown",
                     "contentUrl": f"{base}/knowledge-base.md", "name": "Knowledge base"},
                ],
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def render_catalogue(index, patches, base, source, appstore):
    plugins = [e for e in index.get("plugins", []) if e.get("tier") in ("A", "B")]
    labels = _label_map(index)
    generated = index.get("generated_at", "")
    day = generated[:10]

    # Each plugin appears once, under its first category. Repeating an entry in
    # every category it matches would put the same paragraph on the page three
    # times, which is the one thing a search engine reliably punishes.
    grouped = {}
    for entry in sorted(plugins, key=lambda e: (-e.get("stars", 0), e["id"].lower())):
        cats = entry.get("categories") or ["misc"]
        grouped.setdefault(cats[0], []).append(entry)

    order = [c["id"] for c in index.get("categories", []) if c["id"] in grouped]
    order += [c for c in grouped if c not in order]

    documented = [p for p in patches if p.get("tier") == "B"]
    documented.sort(key=lambda e: (-e.get("repo_stars", 0), e["id"].lower()))
    shown_patches = documented[:PATCH_LIMIT]

    toc = "".join(
        f'<li><a href="#{_esc(cid)}">{_esc(labels.get(cid, cid))}</a> '
        f'<span class="count">{len(grouped[cid])}</span></li>'
        for cid in order
    )
    if shown_patches:
        toc += '<li><a href="#patches">User patches</a> ' \
               f'<span class="count">{len(shown_patches)}</span></li>'

    sections = []
    for cid in order:
        entries = "\n".join(_entry_html(e, labels) for e in grouped[cid])
        count = len(grouped[cid])
        sections.append(
            f'<section>\n<h2 id="{_esc(cid)}">{_esc(labels.get(cid, cid))} '
            f'<span class="count">{count} plugin{"" if count == 1 else "s"}</span></h2>\n'
            f'{entries}\n</section>'
        )

    if shown_patches:
        more = ""
        if len(documented) > len(shown_patches):
            more = (f'<p class="meta">{len(documented) - len(shown_patches)} further documented '
                    f'patches are in <a href="patches.json">patches.json</a> and the '
                    f'<a href="./?tab=patches">patches tab</a>.</p>')
        items = "\n".join(_patch_html(p, labels) for p in shown_patches)
        sections.append(
            '<section>\n<h2 id="patches">User patches '
            f'<span class="count">{len(shown_patches)} of {index["counts"]["patches"]} indexed</span></h2>\n'
            '<p>A patch changes KOReader\'s own behaviour rather than adding a plugin, '
            'and one written for an older release can break after an update. Listed here '
            'are the ones whose file header documents what they do.</p>\n'
            f'{more}<ul class="patches">\n{items}\n</ul>\n</section>'
        )

    description = (
        f"Every recommendable KOReader plugin and user patch, with what each one does: "
        f"{len(plugins)} plugins and {len(shown_patches)} documented patches, "
        f"rebuilt nightly from GitHub."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KOReader plugin catalogue — every indexed plugin and patch</title>
<meta name="description" content="{_esc(description)}">
<link rel="canonical" href="{base}/catalogue.html">
<meta name="robots" content="index,follow,max-snippet:-1">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<meta property="og:type" content="website">
<meta property="og:site_name" content="KOReader Plugin Finder">
<meta property="og:title" content="KOReader plugin catalogue">
<meta property="og:description" content="{_esc(description)}">
<meta property="og:url" content="{base}/catalogue.html">
<meta property="og:image" content="{base}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{base}/og.png">
<script type="application/ld+json">
{_json_ld(index, patches, base, source)}
</script>
<style>{CATALOGUE_CSS}</style>
</head>
<body>
<div class="wrap">

<h1>KOReader plugin catalogue</h1>
<p class="tagline">Every plugin and patch worth recommending, and what each one does.</p>

<div class="lead">
  <p>This is the plain-HTML listing of the
    <a href="./">KOReader Plugin Finder</a>. The finder searches the same
    catalogue by what you want to do rather than what a plugin is called; this
    page exists so the contents can be read, linked and indexed without
    JavaScript.</p>
  <p>Rebuilt nightly from GitHub. Index generated
    <time datetime="{_esc(generated)}">{_esc(day)}</time> —
    {index['counts']['plugins']} plugins and {index['counts']['patches']} patch files
    in total, of which the {len(plugins)} maintained and documented ones are listed
    below. Dormant, archived, undocumented and unstarred forks are left out; they are
    still browsable in the <a href="./">finder</a>.</p>
  <p>Descriptions are extracted from each repository's own README by rule. No model
    writes them, and no entry here is an endorsement — every one carries its status.</p>
  <ul class="toc">{toc}</ul>
</div>

<div class="jump">
  <form action="./" method="get" role="search">
    <input type="search" name="q" autocomplete="off"
           aria-label="Search the plugin finder"
           placeholder="Search {len(plugins)} plugins in the finder — sync, manga, opds">
    <button type="submit">Search</button>
  </form>
  <a class="ai" href="./?ai=1" title="Describe what you need in your own words">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z"/><path d="M18 14l.9 2.1 2.1.9-2.1.9L18 20l-.9-2.1-2.1-.9 2.1-.9L18 14z"/></svg>
    AI<span> Mode</span>
  </a>
</div>

{chr(10).join(sections)}

<footer>
  <a href="./">Search page</a> ·
  <a href="knowledge-base.md">Knowledge base for assistants</a> ·
  <a href="llms.txt">llms.txt</a> ·
  <a href="{source}">Source &amp; curation</a> ·
  <a href="{appstore}">AppStore catalogue</a>
</footer>

</div>
</body>
</html>
"""


def render_robots(base):
    """Everything is public; the only thing worth steering is crawl budget.

    `detail/` is 700-odd small JSON files the page fetches when a reader opens
    a README. Nothing links to them as pages and none of them is a destination,
    so crawling them costs requests on both sides and returns nothing. The rest
    -- index.json, patches.json, knowledge-base.md, llms.txt -- is deliberately
    open, because being read by assistants is a purpose of this project rather
    than a leak from it.

    One caveat worth knowing: this is a project Pages site, so the file lands at
    /koreader-plugin-index/robots.txt and crawlers only read the one at the host
    root. It is generated anyway -- it is where people look, and it costs
    nothing -- but for it to bind, these lines belong in the robots.txt of the
    omer-faruq.github.io repository, with the paths prefixed. The sitemap can be
    submitted directly in Search Console instead, which does not go through
    robots.txt at all.
    """
    return (
        "# https://github.com/omer-faruq/koreader-plugin-index\n"
        "# Project Pages site: crawlers read https://omer-faruq.github.io/robots.txt,\n"
        "# not this one. Mirror these rules there, path-prefixed, to make them bind.\n"
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /detail/\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


def render_sitemap(base, generated):
    """The two HTML pages, with the date the index behind them was rebuilt.

    knowledge-base.md is deliberately absent. It is the same catalogue in
    another format, and asking for both to be indexed is asking to be judged on
    duplicate content; it stays reachable through llms.txt and the footer.
    """
    pages = [(f"{base}/", "daily", "1.0"), (f"{base}/catalogue.html", "daily", "0.8")]
    urls = "\n".join(
        f"  <url>\n"
        f"    <loc>{loc}</loc>\n"
        f"    <lastmod>{generated}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        f"  </url>"
        for loc, freq, priority in pages
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
