/* Ranking parity check.
 *
 * The search page reimplements scripts/rank.py in JavaScript. The quality
 * suite measures the Python side, so if the two drift the tests stop
 * describing what users actually see. This extracts the scorer straight out of
 * docs/index.html and prints its top-3 per query; compare_parity.py diffs that
 * against the Python ranking.
 *
 *   node tests/parity.mjs > /tmp/js.json
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

const html = readFileSync(join(root, "docs", "index.html"), "utf8");
const index = JSON.parse(readFileSync(join(root, "docs", "index.json"), "utf8"));

// Pull the live scorer out of the page rather than copying it here: a copy
// would be a third implementation able to drift from both of the others.
const start = html.indexOf("const WEIGHT =");
const end = html.indexOf("/* ------------------------------------------------------------------- state */");
if (start < 0 || end < 0) {
  console.error("could not locate the ranking block in docs/index.html");
  process.exit(2);
}
const source = html.slice(start, end);

const scorer = new Function(`
  ${source}
  return { tokenise, queryWords, score };
`)();

const cases = readFileSync(join(root, "tests", "queries.toml"), "utf8")
  .split("[[case]]").slice(1)
  .map(block => /query\s*=\s*"([^"]+)"/.exec(block)?.[1])
  .filter(Boolean);

const out = {};
for (const query of cases) {
  const tokens = scorer.tokenise(query);
  const raw = scorer.queryWords(query);
  out[query] = index.plugins
    .map(e => [scorer.score(e, tokens, raw), e])
    .filter(p => p[0] > 0)
    .sort((a, b) => b[0] - a[0] || a[1].id.localeCompare(b[1].id))
    .slice(0, 3)
    .map(p => p[1].id);
}
console.log(JSON.stringify(out, null, 2));
