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
//
// One rule is worth the trouble to model properly: an element that is not on
// the page returns null. Half of this dialog is buttons written into aiOut by
// one render and gone by the next, and code that reaches for one after it has
// been rendered away is exactly the mistake worth catching here. So ids in the
// static markup always resolve, ids currently written into some element's
// innerHTML resolve until the next render, and everything else is null.
const staticIds = new Set(
  [...html.slice(0, html.lastIndexOf("<script>")).matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const els = new Map();    // static: they outlive every render
const live = new Map();   // written by a render, dropped by the next one
const makeEl = id => {
  let inner = "";
  return {
    id, textContent: "", value: "", hidden: false, disabled: false,
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    style: {}, checked: false,
    get innerHTML() { return inner; },
    // Any write is a render, and a render invalidates whatever the last one
    // put on the page.
    set innerHTML(v) { inner = v; live.clear(); },
    addEventListener(type, fn) { (this._on ||= {})[type] = fn; },
    removeEventListener() {},
    querySelectorAll: () => [],
    querySelector: () => null,
    getBoundingClientRect: () => ({ top: 0 }),
    // Returns whatever the handler does, so a test can await an async one.
    setAttribute() {}, removeAttribute() {}, focus() {}, click() { return this._on?.click?.(); },
    scrollIntoView() {}, appendChild() {}, remove() {}
  };
};
const lookup = id => {
  if (staticIds.has(id)) {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  }
  if (live.has(id)) return live.get(id);
  const onPage = [...els.values()].some(e => e.innerHTML.includes(`id="${id}"`));
  if (!onPage) return null;
  live.set(id, makeEl(id));
  return live.get(id);
};
const document = {
  getElementById: lookup,
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
  // A refusal the page is meant to read rather than merely report. The body is
  // the whole point: which limit was hit decides whether asking again is worth
  // a second request.
  if (step.fail) {
    return { ok: false, status: step.fail.status, text: async () => step.fail.body };
  }
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
// For a narrowed round, where the point is that the model may drop what it
// chose before: the last candidate is always one retrieval has just turned up.
const takeLast = fit => user => {
  const id = user.candidates.at(-1).id;
  chosen.push(id);
  return { picks: [{ id, fit, why: "meets the condition" }] };
};
// Goes through the rendered control rather than calling lookAgain directly,
// so the wiring is part of what is tested. A missing box is a failure to
// report, not an exception to die on -- the checks after it still say
// something.
const narrow = async text => {
  const box = document.getElementById("aiNarrow");
  if (!box) return check("the narrowing box is on the page", false, out().slice(0, 160));
  box.value = text;
  await document.getElementById("aiNarrowGo").click();
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

// === 7. the reader says what is missing ===================================
store.set("kpi.tuning", JSON.stringify({ retries: 0 }));
script = [
  { kind: "expand", reply: { terms: ["sync"], language: "English" } },
  { kind: "weigh", reply: takeFirst(70) }
];
document.getElementById("aiQuestion").value = "sync my highlights";
await api.runAI();
const before = ids()[0];

script = [
  { kind: "angle", reply: { terms: ["offline", "local"] } },
  { kind: "weigh", reply: takeLast(90) }
];
await narrow("must work offline");

check("narrowing reaches the angle call", calls.at(-2).user.missing === "must work offline",
  JSON.stringify(calls.at(-2).user.missing));
check("narrowing reaches the weigh call", calls.at(-1).user.refinement === "must work offline",
  JSON.stringify(calls.at(-1).user.refinement));
check("REFINE_RULE sent with it",
  calls.at(-1).system.includes("It narrows the question"));
// The picks were judged against a question that did not carry the condition,
// so they go back in to be judged again rather than being kept on trust.
check("earlier pick reopened as a candidate",
  calls.at(-1).user.candidates.some(c => c.id === before), before);
check("no already_found on a narrowed round",
  calls.at(-1).user.already_found === undefined,
  JSON.stringify(calls.at(-1).user.already_found));
check("RETRY_FOLLOWUP withheld when nothing is being kept",
  !calls.at(-1).system.includes("are kept"));
check("a pick that fails the narrowing leaves the list",
  !ids().includes(before), ids().join());
check("the narrowing is shown with the answer",
  out().includes('narrowed by &quot;must work offline&quot;'), out().slice(0, 300));
check("the shared question carries the narrowing",
  api.state.answer.question === "sync my highlights — must work offline",
  api.state.answer.question);
check("two requests, same as a first ask", script.length === 0, "left " + script.length);

// === 8. a second narrowing adds a condition, it does not replace one =======
script = [
  { kind: "angle", reply: { terms: ["kobo"] } },
  { kind: "weigh", reply: takeLast(80) }
];
await narrow("Kobo only");
check("narrowings accumulate",
  calls.at(-1).user.refinement === "must work offline; Kobo only",
  JSON.stringify(calls.at(-1).user.refinement));
check("both are shown", out().includes("must work offline") && out().includes("Kobo only"));

// === 9. a narrowed round that fails gives the reader back what they had ====
const held = ids();
script = [
  { kind: "angle", reply: { terms: ["dropbox"] } },
  { kind: "weigh", throw: true }
];
await narrow("free only");
check("the picks come back", ids().join() === held.join(), ids().join() + " vs " + held.join());
check("the failed narrowing is not kept",
  !api.session.refinements.includes("free only"), JSON.stringify(api.session.refinements));
check("the failure is reported", /couldn|error|reach|refus|fail|wrong/i.test(out()),
  out().slice(0, 200));

// === 10. an empty box is still the old blind round =========================
script = [
  { kind: "angle", reply: { terms: ["calibre"] } },
  { kind: "weigh", reply: takeLast(85) }
];
await narrow("   ");
check("whitespace is not a narrowing", calls.at(-2).user.missing === undefined,
  JSON.stringify(calls.at(-2).user.missing));
check("blind round keeps the earlier picks",
  Array.isArray(calls.at(-1).user.already_found) && calls.at(-1).user.already_found.length > 0);

// === 11. a per-minute token ceiling shortens the question ==================
// The free tiers that have one set it below what a full shortlist costs, so
// the choice is a shorter question or none at all. The numbers come from the
// provider: 8000 allowed against 9421 asked, over 40 candidates, is 30.
script = [
  { kind: "expand", reply: { terms: ["sync", "highlights"], language: "English" } },
  { kind: "weigh", fail: { status: 429, body: JSON.stringify({ error: {
      message: "Rate limit reached for model `x` on tokens per minute (TPM): "
             + "Limit 8000, Used 0, Requested 9421. Please try again in 10s.",
      type: "rate_limit_exceeded" } }) } },
  { kind: "weigh", reply: takeFirst(80) }
];
document.getElementById("aiQuestion").value = "sync my highlights everywhere";
await api.runAI();
const full = calls.at(-2).user.candidates.length;
const short = calls.at(-1).user.candidates.length;
check("the ceiling costs exactly one extra request", script.length === 0, "left " + script.length);
check("the second ask is shorter", short < full, short + " vs " + full);
check("shortened by what the provider allowed", short === 30, String(short));
check("an answer comes back", ids().length === 1, out().slice(0, 160));
check("the answer says it was shortened", /shorter shortlist/i.test(out()), out().slice(0, 200));
check("only what was sent is counted as weighed",
  api.session.weighed === short, api.session.weighed + " vs " + short);

// === 12. a 429 that asking for less cannot fix is not asked twice ==========
// OpenAI puts an empty account under the same status as a rate limit and
// separates them only in the body. Retrying that one spends a second request
// to be told the same thing, which on a fifty-a-day key is a real price.
script = [
  { kind: "expand", reply: { terms: ["zotero"], language: "English" } },
  { kind: "weigh", fail: { status: 429, body: JSON.stringify({ error: {
      message: "You exceeded your current quota, please check your plan and billing details.",
      type: "insufficient_quota" } }) } }
];
document.getElementById("aiQuestion").value = "access my zotero library";
await api.runAI();
check("a billing 429 is not retried", script.length === 0, "left " + script.length);
check("and is named as credit, not as speed", /out of credit/i.test(out()), out().slice(0, 200));
check("the provider's own words survive",
  /check your plan and billing/i.test(out()), out().slice(0, 240));

// === 13. the same ceiling, refused before the minute rather than during it =
// Groq answers a question it could never fit with 413 rather than 429, and
// spends the first hundred and fifty characters naming the model, the
// organisation and the service tier before saying what the limit was. Both
// halves are load-bearing: the status has to be recognised, and the body has
// to be kept long enough to still contain the numbers.
script = [
  { kind: "expand", reply: { terms: ["anki"], language: "English" } },
  { kind: "weigh", fail: { status: 413, body: JSON.stringify({ error: {
      message: "Request too large for model `openai/gpt-oss-120b` in organization "
             + "`org_01jy8ghtpjfak8cgck2we4bp3t` service tier `on_demand` on tokens per "
             + "minute (TPM): Limit 8000, Requested 10254, please reduce your message size "
             + "and try again.",
      type: "tokens", code: "rate_limit_exceeded" } }) } },
  { kind: "weigh", reply: takeFirst(88) }
];
document.getElementById("aiQuestion").value = "anki integration for vocabulary";
await api.runAI();
const refused = calls.at(-2).user.candidates.length;
const accepted = calls.at(-1).user.candidates.length;
check("a 413 is treated as the ceiling it is", script.length === 0, "left " + script.length);
check("and the numbers survived the body being cut", accepted === 28, String(accepted));
check("which is smaller than what was refused", accepted < refused, accepted + " vs " + refused);
check("an answer comes back", ids().length === 1, out().slice(0, 160));
check("the answer owns up to the shorter list", /shorter shortlist/i.test(out()), out().slice(0, 200));

// === 14. a spent minute is not a big question, and says how long to wait ===
// The trim cannot help here: the request is not too large, the window is
// already used up. Groq puts the only actionable thing in the body at the very
// end, past where the sentence shown to anybody is cut, so it is pulled out
// and said first.
script = [
  { kind: "expand", reply: { terms: ["zotero"], language: "English" } },
  { kind: "weigh", fail: { status: 429, body: JSON.stringify({ error: {
      message: "Rate limit reached for model `openai/gpt-oss-120b` in organization "
             + "`org_01jy8ghtpjfak8cgck2we4bp3t` service tier `on_demand` on tokens per "
             + "minute (TPM): Limit 8000, Used 7480, Requested 6300. Please try again in 7.2s.",
      type: "tokens", code: "rate_limit_exceeded" } }) } }
];
document.getElementById("aiQuestion").value = "access my zotero library please";
await api.runAI();
// 6300 asked against a limit of 8000: the question already fits, and a
// shorter one would fit no better. The trim declines rather than spending a
// request to prove it, which is the whole reason it reads the numbers.
check("a request that already fits is not shortened", script.length === 0, "left " + script.length);
check("a spent window is named as time, not size", /allowance is spent/i.test(out()), out().slice(0, 200));
check("and the wait is rounded up off the decimal", /in 8 seconds/.test(out()), out().slice(0, 220));
check("the model and organisation are not what leads", !/^\s*<div class="note"><b>[^<]*org_/.test(out()));

// === 15. the page says it is working while it waits =======================
// The only check here that looks at the screen mid-flight rather than after:
// the scripted model reads aiOut at the moment it is called, which is exactly
// when a reader on a slow free tier is looking at it and wondering whether
// anything is happening.
const midFlight = [];
script = [
  { kind: "expand", reply: user => { midFlight.push(out()); return { terms: ["opds"], language: "English" }; } },
  { kind: "weigh", reply: user => { midFlight.push(out()); return takeFirst(80)(user); } }
];
document.getElementById("aiQuestion").value = "browse an opds catalog";
await api.runAI();
check("waiting while the question is read", /class="state working"/.test(midFlight[0] || ""),
  (midFlight[0] || "").slice(0, 120));
check("waiting while the candidates are weighed", /class="state working"/.test(midFlight[1] || ""),
  (midFlight[1] || "").slice(0, 120));
check("the moving part is there to be seen", /<span class="dots"[^>]*>(<i><\/i>){3}<\/span>/.test(midFlight[1] || ""),
  (midFlight[1] || "").slice(0, 160));
check("it says what it is waiting for", /Weighing \d+ candidates/.test(midFlight[1] || ""),
  (midFlight[1] || "").slice(0, 160));
check("and it is gone once there is an answer", !/state working/.test(out()), out().slice(0, 120));

// Quiet when it passes, for the same reason parity_check.py is: a nightly log
// nobody reads is a log that hides the one line that mattered.
const failures = t.filter(line => line.startsWith("FAIL"));
if (failures.length) {
  console.error(t.join("\n"));
  process.exit(1);
}
console.log(`AI Mode flow ok across ${t.length} checks`);
