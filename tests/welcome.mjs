/* Welcome-line check.
 *
 * The line is the only thing on the page written in a language nobody here
 * can proofread by looking at the page, and the only thing whose audience is
 * decided by a header rather than by a click. Both halves are worth pinning
 * down: that every locale KOReader ships lands on a line, and that an English
 * reader gets nothing.
 *
 *   node tests/welcome.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "docs", "index.html"), "utf8");

// The block, lifted out of the page rather than copied: a copy would be a
// second table free to drift from the one readers see.
const start = html.indexOf("const WELCOME = {");
const end = html.indexOf("function welcomeFor");
const close = html.indexOf("\n}", html.indexOf("function welcomeFor"));
if (start < 0 || end < 0) {
  console.error("could not locate the welcome block in docs/index.html");
  process.exit(2);
}
const { WELCOME, WELCOME_RTL, welcomeFor } = new Function(`
  ${html.slice(start, close + 2)}
  return { WELCOME, WELCOME_RTL, welcomeFor };
`)();

const t = [];
const check = (name, cond, extra = "") =>
  t.push((cond ? "ok   " : "FAIL ") + name + (cond ? "" : "  << " + extra));

// Every locale KOReader offers in its language menu, as it writes them.
const KOREADER = [
  "C", "en_GB", "ca", "cs", "de", "eo", "es", "eu", "fi", "fr", "ga", "gl",
  "hr", "id", "it_IT", "hu", "lt_LT", "lv", "nl_NL", "nb_NO", "pl", "pl_PL",
  "pt_PT", "pt_BR", "ro", "ro_MD", "sk", "sl", "sv", "tr", "vi", "ar",
  "bg_BG", "bn", "el", "fa", "he", "hi", "ja", "ka", "kk", "ko_KR", "ml",
  "ru", "sr", "uk", "zh", "zh_CN", "zh_TW", "zh_TW.Big5",
  // Shipped as complete translations, left out of the menu.
  "da", "th"
];

// A browser sends BCP 47, KOReader stores POSIX. Same languages, different
// punctuation, and the page has to cope with what the browser sends.
const asBrowserTag = locale => locale.split(".")[0].replace("_", "-");

const missing = KOREADER
  .filter(l => l !== "C" && !l.startsWith("en"))
  .filter(l => !welcomeFor([asBrowserTag(l)]));
check("every KOReader locale reaches a line", !missing.length, missing.join(", "));

check("English gets nothing", !welcomeFor(["en-GB"]) && !welcomeFor(["en"]));
// The first understood tag wins, not the first tag: a browser sending
// "en-US,tr" is asking for English and must not be handed Turkish.
check("English first means English", !welcomeFor(["en-US", "tr"]));
check("an unknown first tag falls through",
  (welcomeFor(["kw", "tr"]) || {}).lang === "tr", JSON.stringify(welcomeFor(["kw", "tr"])));
check("nothing understood means nothing shown", !welcomeFor(["kw", "xx"]));
check("no tags at all is safe", !welcomeFor([]) && !welcomeFor([undefined, ""]));

// Regional tags that are a different line, and regional tags that are not.
const lang = tag => (welcomeFor([tag]) || {}).lang;
check("pt-BR has its own line", lang("pt-BR") === "pt-br", String(lang("pt-BR")));
check("pt-PT falls back to pt", lang("pt-PT") === "pt", String(lang("pt-PT")));
check("de-AT falls back to de", lang("de-AT") === "de", String(lang("de-AT")));
check("zh-Hant is traditional", lang("zh-Hant") === "zh-tw", String(lang("zh-Hant")));
check("zh-HK is traditional", lang("zh-HK") === "zh-tw", String(lang("zh-HK")));
check("zh-Hans is simplified", lang("zh-Hans") === "zh", String(lang("zh-Hans")));
check("zh-CN is simplified", lang("zh-CN") === "zh", String(lang("zh-CN")));
check("nynorsk reads bokmal", lang("nn-NO") === "nb", String(lang("nn-NO")));
check("case does not matter", lang("TR-tr") === "tr", String(lang("TR-tr")));

// Content, not just coverage.
const codes = Object.keys(WELCOME);
// A header line, so it has to stay one. Long enough to carry both halves,
// short enough not to wrap into a paragraph above the search bar.
const lengths = codes.filter(c => WELCOME[c].length < 25 || WELCOME[c].length > 120);
check("every line is a line", !lengths.length,
  lengths.map(c => `${c}:${WELCOME[c].length}`).join(", "));

// The line sits two elements above a keyword box that scores against an
// English index. One that opens with "you can write in your own language" is
// an invitation to type into the one control on the page that would silently
// return nothing, so the button has to come first and the box has to be ruled
// out. Only the first half is checkable here; the second is prose.
const buried = codes.filter(c => WELCOME[c].indexOf("AI Mode") > 20);
check("every line leads with the button", !buried.length,
  buried.map(c => `${c}@${WELCOME[c].indexOf("AI Mode")}`).join(", "));
// The button reads "AI Mode" and nothing else on the page is translated, so a
// line that renders it as "le mode IA" leaves the reader hunting for a control
// that is not there.
check("every line names the button as it is labelled",
  codes.every(c => WELCOME[c].includes("AI Mode")),
  codes.filter(c => !WELCOME[c].includes("AI Mode")).join(", "));
check("the RTL set is a subset of the lines",
  [...WELCOME_RTL].every(c => WELCOME[c]), [...WELCOME_RTL].join(", "));
// A line whose script is right-to-left but which is not marked as such would
// render its dashes and Latin fragments on the wrong side.
const rtlish = codes.filter(c => /[֐-׿؀-ۿ]/.test(WELCOME[c]));
check("every RTL script is marked RTL",
  rtlish.every(c => WELCOME_RTL.has(c.split("-")[0])), rtlish.join(", "));
check("no LTR line is marked RTL",
  [...WELCOME_RTL].every(c => /[֐-׿؀-ۿ]/.test(WELCOME[c] || "")));

// --- and the part that puts it on the page -------------------------------
// Coverage is only half of it: a line with no `dir` renders its em dash and
// its Latin "AI Mode" at the wrong end of an Arabic sentence, and a line left
// hidden is no line at all.
// The whole block, table included: the part that paints is an IIFE that
// closes over the rest, so it cannot be lifted out on its own.
const paint = html.slice(start, html.indexOf("// Keeps the address bar honest"));
const show = languages => {
  const el = { lang: "", dir: "", textContent: "", hidden: true };
  new Function("document", "navigator", paint)(
    { getElementById: id => (id === "welcome" ? el : null) },
    { languages, language: languages[0] });
  return el;
};

const tr = show(["tr-TR", "en-US"]);
check("the line is unhidden", tr.hidden === false);
check("the line carries its language", tr.lang === "tr", tr.lang);
check("a left-to-right line has no dir", !tr.dir, tr.dir);
check("the text is set as text, not markup", tr.textContent === WELCOME.tr, tr.textContent);

const ar = show(["ar-EG"]);
check("an RTL line is marked rtl", ar.dir === "rtl", ar.dir);
const he = show(["he-IL"]);
check("Hebrew is marked rtl", he.dir === "rtl", he.dir);

const en = show(["en-US", "tr"]);
check("an English reader is left alone", en.hidden === true && !en.textContent);
check("no languages list at all is survivable", show([]).hidden === true);

const br = show(["pt-BR"]);
check("the Brazilian line is the Brazilian one", br.textContent === WELCOME["pt-br"], br.lang);

const failures = t.filter(line => line.startsWith("FAIL"));
if (failures.length) {
  console.error(t.join("\n"));
  process.exit(1);
}
console.log(`welcome line ok across ${t.length} checks, ${codes.length} languages`);
