/* AI Mode flow check.
 *
 * The dialog spends somebody else's API key, so its rules are about restraint
 * as much as results: how many requests a question costs, which candidates the
 * model is shown twice, what happens to an answer when a later round fails.
 * None of that is visible in a rendered page and none of it can be tried by
 * hand without a key, so it is driven here -- the real page script, a stub DOM
 * and a scripted model that replies on cue.
 *
 *   node tests/ai_flow.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "docs", "index.html"), "utf8");
const index = JSON.parse(readFileSync(join(root, "docs", "index.json"), "utf8"));

// Run against the live page for the same reason parity.mjs does: a copy of
// this logic here would be a second implementation free to drift from the one
// people actually use. Only boot() is dropped -- it fetches the index.
const body = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)[1]
  .replace(/\nboot\(\);\s*$/, "\n");

// --- stub DOM -------------------------------------------------------------
// Enough of a document for the dialog to write into and read back, and no
// more. Nothing here is asserted on; the assertions are all about what went
// to the model and what came back out as HTML.
const els = new Map();
const makeEl = id => ({
  id, innerHTML: "", textContent: "", value: "", hidden: false, disabled: false,
  dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
  style: {}, checked: false,
  addEventListener(type, fn) { (this._on ||= {})[type] = fn; },
  removeEventListener() {},
  querySelectorAll: () => [],
  querySelector: () => null,
  getBoundingClientRect: () => ({ top: 0 }),
  setAttribute() {}, removeAttribute() {}, focus() {}, click() { this._on?.click?.(); },
  scrollIntoView() {}, appendChild() {}, remove() {}
});
const document = {
  getElementById: id => els.get(id) || (els.set(id, makeEl(id)), els.get(id)),
  querySelectorAll: () => [], querySelector: () => null,
  addEventListener() {}, createElement: () => makeEl("tmp"),
  documentElement: makeEl("html"), body: makeEl("body"),
  title: "", head: makeEl("head")
};
const store = new Map();
const localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)
};
const location = { origin: "https://x", pathname: "/", hash: "", search: "" };
const history = { replaceState() {} };
const window = {
  location, history, localStorage, matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {}, scrollTo() {}, innerHeight: 800, scrollY: 0,
  requestAnimationFrame: fn => fn()
};
const navigator = { clipboard: { writeText: async () => {} }, language: "en" };

// --- scripted model -------------------------------------------------------
// Every request is booked in advance. A call the script did not expect throws
// rather than returning something plausible, because "how many requests did
// that cost" is one of the things being tested.
const calls = [];
let script = [];
async function fakeFetch(url, opts) {
  const sent = JSON.parse(opts.body);
  const system = sent.messages[0].content;
  const user = JSON.parse(sent.messages[1].content);
  const step = script.shift();
  if (!step) throw new Error("model called more times than the script allows");
  calls.push({ kind: step.kind, system, user });
  if (step.throw) return { ok: false, status: 500, text: async () => "boom" };
  const reply = typeof step.reply === "function" ? step.reply(user) : step.reply;
  return {
    ok: true,
    json: async () => ({ choices: [{ message: { content: JSON.stringify(reply) } }] })
  };
}

const api = new Function(
  "document", "window", "localStorage", "location", "history", "navigator", "fetch",
  body + "\n; return { state, runAI, lookAgain, get session() { return aiSession; }, renderAnswer, $ };"
)(document, window, localStorage, location, history, navigator, fakeFetch);

api.state.index = index;
api.state.index.patches = [];
store.set("kpi.ai", JSON.stringify({ provider: "deepseek", model: "m", key: "k" }));

const out = () => document.getElementById("aiOut").innerHTML;
const ids = () => [...out().matchAll(/data-install="([^"]+)"/g)].map(m => m[1]);
// Picked out of the shortlist the model is actually shown, so the test does
// not depend on which entries retrieval happens to surface.
const chosen = [];
const takeFirst = fit => user => {
  const id = user.candidates[0].id;
  chosen.push(id);
  return { picks: [{ id, fit, why: "because" }] };
};

const t = [];
const check = (name, cond, extra = "") =>
  t.push((cond ? "ok   " : "FAIL ") + name + (cond ? "" : "  << " + extra));

// === 1. first ask, model is satisfied, weak best fit ======================
script = [
  { kind: "expand", reply: { terms: ["sync", "highlights"], language: "English" } },
  { kind: "weigh", reply: takeFirst(40) }
];
document.getElementById("aiQuestion").value = "sync my highlights";
await api.runAI();

check("answer rendered", ids().length === 1, ids().join());
check("look-again offered", out().includes('id="aiAgain"'));
check("weak fit offers other catalogue", out().includes('id="aiOtherTab"'));
check("no automatic second round", script.length === 0, "left " + script.length);
check("session holds tried terms",
  JSON.stringify(api.session.tried) === '["sync","highlights"]', JSON.stringify(api.session.tried));
check("weighed counted", api.session.weighed > 0, String(api.session.weighed));
const firstSeen = api.session.byId.size;

// === 2. reader asks for another angle =====================================
script = [
  { kind: "angle", reply: { terms: ["sync", "webdav", "cloud"] } },   // 'sync' is already tried
  { kind: "weigh", reply: takeFirst(95) }
];
await api.lookAgain(api.session);

check("angle prompt got the tried list",
  JSON.stringify(calls.at(-2).user.tried) === '["sync","highlights"]',
  JSON.stringify(calls.at(-2).user.tried));
check("already-tried word dropped",
  !api.session.searchedAgainFor.at(-1).includes("sync"),
  JSON.stringify(api.session.searchedAgainFor.at(-1)));
check("second round carries already_found",
  Array.isArray(calls.at(-1).user.already_found) && calls.at(-1).user.already_found.length === 1);
check("second round told it is a follow-up",
  calls.at(-1).system.includes("This is another shortlist"));
check("RETRY_RULE withdrawn on a manual round",
  !calls.at(-1).system.includes("If the shortlist plainly does not contain"));
// The rule and the reply shape have to go together: a model shown the key is
// a model that fills it, whatever the prose above says.
check("reply shape drops search_again with the rule",
  !calls.at(-1).system.includes("search_again"), calls.at(-1).system.slice(-260));
check("reply shape keeps search_again while a round remains",
  calls.find(c => c.kind === "weigh").system.includes('"search_again":["term"]'));
check("candidates were unseen ones", api.session.byId.size > firstSeen,
  firstSeen + " -> " + api.session.byId.size);
check("second round saw only unseen entries",
  !calls.at(-1).user.candidates.some(c => c.id === chosen[0]));
check("both picks kept, better one first",
  ids()[0] === chosen[1] && ids().includes(chosen[0]), ids().join() + " vs " + chosen.join());
check("strong best fit withdraws the catalogue offer",
  !out().includes('id="aiOtherTab"'));
check("search line names both searches",
  out().includes("searched for") && out().includes("then for"), out().slice(0, 200));
check("share state updated", (api.state.answer || {}).picks?.length === 2,
  JSON.stringify(api.state.answer));
check("model calls used", script.length === 0, "left " + script.length);

// === 3. a manual round that fails keeps the answer and says so ============
script = [{ kind: "angle", throw: true }];
await api.lookAgain(api.session);
check("failure is reported", out().includes("<b>") && /couldn|error|reach|refus|fail|wrong/i.test(out()),
  out().slice(0, 240));
check("failure keeps the picks", ids().length === 2, ids().join());
check("failure note is not sticky (rerender)",
  (api.session.failed === null), String(api.session.failed));

// === 4. model returns only words already tried ============================
script = [{ kind: "angle", reply: { terms: ["sync", "webdav"] } }];
await api.lookAgain(api.session);
check("no-new-words path spends only one request", script.length === 0);
check("no-new-words is explained", out().includes("No other angle to try"), out().slice(0, 200));
check("picks survive", ids().length === 2);

// === 5. empty answer still offers both ways out ===========================
store.set("kpi.tuning", JSON.stringify({ retries: 0 }));
script = [
  { kind: "expand", reply: { terms: ["zzz"], language: "English" } },
  { kind: "weigh", reply: { picks: [], note: "nothing does this" } }
];
document.getElementById("aiQuestion").value = "make me a sandwich";
await api.runAI();
check("empty answer offers another angle", out().includes('id="aiAgain"'));
check("empty answer offers the other catalogue", out().includes('id="aiOtherTab"'));
check("new question reset the session",
  api.session.tried.join() === "zzz", JSON.stringify(api.session.tried));

// === 6. an answer left over from the other tab is not offered another round =
store.set("kpi.tuning", JSON.stringify({ retries: 0 }));
api.state.index.patches = index.plugins.slice(0, 30)
  .map(e => ({ ...e, repo_stars: e.stars, path: e.id + ".lua" }));
script = [
  { kind: "expand", reply: { terms: ["sync"], language: "English" } },
  { kind: "weigh", reply: takeFirst(90) }
];
document.getElementById("aiQuestion").value = "sync my highlights";
await api.runAI();
check("plugins answer offers a round", out().includes('id="aiAgain"'));

// Close, switch tab, reopen: the old answer is still in the box and gets
// re-rendered as it stands.
const stale = api.session;
api.state.tab = "patches";
api.renderAnswer(stale);
check("no round offered against the other catalogue", !out().includes('id="aiAgain"'),
  out().slice(0, 200));
check("the answer itself is still there", ids().length === 1, ids().join());
api.state.tab = "plugins";

// Quiet when it passes, for the same reason parity_check.py is: a nightly log
// nobody reads is a log that hides the one line that mattered.
const failures = t.filter(line => line.startsWith("FAIL"));
if (failures.length) {
  console.error(t.join("\n"));
  process.exit(1);
}
console.log(`AI Mode flow ok across ${t.length} checks`);
