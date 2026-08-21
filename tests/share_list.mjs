/* Shared-list text check.
 *
 * The list someone copies out of the results is the one thing this page
 * produces that ends up somewhere nobody here can see -- a forum reply, a chat
 * message -- and it has to stand on its own there. Two things make it stand:
 * the fields the reader ticked are the fields that appear, and the line under
 * the heading says how the list was narrowed. Neither is visible in a rendered
 * page, and both are one loop away from silently dropping a column.
 *
 *   node tests/share_list.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "docs", "index.html"), "utf8");

// The real page script, as ai_flow.mjs and parity.mjs run it: a copy of the
// format here would be a second implementation free to drift from the one
// people paste. Only boot() is dropped -- it fetches the index.
const body = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)[1]
  .replace(/\nboot\(\);\s*$/, "\n");

// Nothing is rendered here, so the document only has to be inert: the two
// blocks that run on load look for elements, find none, and stop.
const document = {
  getElementById: () => null, addEventListener() {},
  createElement: () => ({}), documentElement: { getAttribute: () => null, setAttribute() {}, hasAttribute: () => false },
  body: {}
};
const localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const navigator = { languages: ["en"] };
const window = { matchMedia: () => ({ matches: false }) };

const { listShareTextFor } = new Function(
  "document", "localStorage", "navigator", "window",
  `${body}\n return { listShareTextFor };`
)(document, localStorage, navigator, window);

const t = [];
const check = (name, cond, extra = "") =>
  t.push((cond ? "ok   " : "FAIL ") + name + (cond ? "" : "  << " + extra));

const ALL = { purpose: true, url: true, stars: true, tier: true,
              dates: true, cats: true, link: true };
const NONE = { purpose: false, url: false, stars: false, tier: false,
               dates: false, cats: false, link: false };

const plugin = {
  id: "omer-faruq/webbrowser.koplugin", owner: "omer-faruq", repo: "webbrowser.koplugin",
  url: "https://github.com/omer-faruq/webbrowser.koplugin",
  purpose: "Browse and read web pages from KOReader.",
  description: "Text-oriented web browser",
  categories: ["web", "reading"], keywords: [],
  tier: "A", tier_reasons: ["curated"], stars: 143,
  pushed_at: "2026-08-01T00:00:00Z", created_at: "2025-11-04T00:00:00Z"
};
const dormant = {
  id: "someone/old.koplugin", owner: "someone", repo: "old.koplugin",
  url: "https://github.com/someone/old.koplugin",
  purpose: "", description: "An abandoned experiment",
  categories: [], tier: "C", tier_reasons: ["dormant"], stars: 0
};
const patch = {
  id: "omer-faruq/koreader-user-patches:2-fontsize.lua",
  owner: "omer-faruq", repo: "koreader-user-patches", path: "2-fontsize.lua",
  url: "https://github.com/omer-faruq/koreader-user-patches/blob/main/2-fontsize.lua",
  purpose: "Allow font sizes below the built-in minimum.",
  categories: ["ui"], tier: "B", tier_reasons: ["has_header"],
  repo_stars: 118, file_modified_at: "2026-05-02T00:00:00Z"
};

const view = {
  tab: "plugins", query: "web", category: "", sort: "match", showC: false,
  total: 41, generated: "2026-08-22",
  url: "https://omer-faruq.github.io/koreader-plugin-index/?q=web"
};

// --- the fields are the ones that were ticked ------------------------------

const full = listShareTextFor([plugin], ALL, view);
check("purpose is carried", full.includes("Browse and read web pages from KOReader."));
check("the repository link is carried", full.includes(plugin.url));
check("stars are carried", full.includes("★ 143"));
check("the tier is said in words", full.includes("curated"), full);
check("categories are carried", full.includes("web, reading"), full);
check("dates are carried", /updated .+ · added /.test(full), full);
check("the link back is the last line", full.trim().endsWith(view.url), full);

const bare = listShareTextFor([plugin], NONE, view);
check("nothing but the name survives NONE",
  bare.includes("1. omer-faruq/webbrowser.koplugin")
  && !bare.includes(plugin.url) && !bare.includes("★")
  && !bare.includes("curated") && !bare.includes("Browse and read"), bare);
// Unticking the link has to remove the address entirely, not leave a bare
// trailing newline where a reader expects a URL.
check("no link back means no trailing address", !bare.includes("http"), bare);

// GitHub's one-liner is the fallback when the README yielded nothing, which is
// exactly the entry a reader is least able to identify from its name alone.
check("description stands in for a missing purpose",
  listShareTextFor([dormant], ALL, view).includes("An abandoned experiment"));
check("a demoted entry says why it was demoted",
  listShareTextFor([dormant], ALL, view).includes("no updates in a year"),
  listShareTextFor([dormant], ALL, view));

// A patch carries its repository's stars; reading `stars` off it prints 0 for
// every patch in a 118-star repository.
check("a patch shows the repository's stars",
  listShareTextFor([patch], ALL, { ...view, tab: "patches" }).includes("★ 118"));

// --- the line that says how the list was narrowed --------------------------

const three = listShareTextFor([plugin, dormant, patch], ALL, view);
check("a cut list says it was cut", three.includes("First 3 of 41"), three);
check("entries are numbered in order",
  three.indexOf("1. omer-faruq/webbrowser") < three.indexOf("2. someone/old")
  && three.indexOf("2. someone/old") < three.indexOf("3. omer-faruq/koreader-user-patches"));

const whole = listShareTextFor([plugin], ALL, { ...view, total: 1 });
check("an uncut list does not pretend to be cut",
  whole.includes("1 result") && !whole.includes("First"), whole);

check("the query is named in the heading", three.startsWith('KOReader Plugin Finder — plugins matching "web"'), three.split("\n")[0]);
check("a category is named when there is no query",
  listShareTextFor([plugin], ALL, { ...view, query: "", category: "Interface" })
    .startsWith("KOReader Plugin Finder — plugins · Interface"));
check("the tab is named", listShareTextFor([patch], ALL, { ...view, tab: "patches", query: "" })
  .startsWith("KOReader Plugin Finder — patches"));
// A category narrows the list as hard as a query does. Named alongside it, or
// the heading describes a search that was never run.
check("a category survives a query",
  listShareTextFor([plugin], ALL, { ...view, category: "Web" })
    .startsWith('KOReader Plugin Finder — plugins matching "web" · Web'),
  listShareTextFor([plugin], ALL, { ...view, category: "Web" }).split("\n")[0]);
// Past the ninth entry a fixed indent slides under the number and the item
// stops looking like one block.
const ten = listShareTextFor(Array.from({ length: 10 }, () => plugin), ALL, view);
check("the tenth entry indents past its own number",
  ten.includes("10. omer-faruq/webbrowser.koplugin\n    Browse and read"),
  ten.slice(ten.indexOf("10. ")));
check("a non-default order is stated",
  listShareTextFor([plugin], ALL, { ...view, sort: "updated" }).includes("most recently updated first"));
check("including dormant entries is stated",
  listShareTextFor([plugin], ALL, { ...view, showC: true }).includes("dormant, forks and stubs included"));
// The list outlives the day it was pasted, and the catalogue is rebuilt every
// night. Undated, it reads as current forever.
check("the index date rides along", three.includes("index 2026-08-22"));

// --- shape -----------------------------------------------------------------

check("no run of blank lines", !/\n\n\n/.test(three), JSON.stringify(three));
check("ends with exactly one newline", three.endsWith("\n") && !three.endsWith("\n\n"));
check("an empty list is still a sentence",
  listShareTextFor([], ALL, { ...view, total: 0 }).includes("0 results"));

console.log(t.join("\n"));
const failed = t.filter(line => line.startsWith("FAIL")).length;
console.log(`\n${t.length - failed}/${t.length} checks passed`);
process.exit(failed ? 1 : 0);
