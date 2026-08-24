"""Deterministic extraction: README text in, structured fields out.

No model runs here. Every field is derived by rule, which is what keeps the
weekly rebuild free, reproducible and free of an API key that would eventually
expire. Where a rule cannot produce something honest it produces nothing --
an empty purpose is correct, an invented one is not.
"""

import datetime
import html
import re

# --------------------------------------------------------------------- markdown

BADGE_RE = re.compile(r"^\s*[\[!]{1,2}\[[^\]]*\]\([^)]*\)\s*$")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
HTML_RE = re.compile(r"<[^>]+>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def clean_markdown(text):
    text = COMMENT_RE.sub(" ", text)
    text = CODE_FENCE_RE.sub(" ", text)
    text = IMAGE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    text = HTML_RE.sub(" ", text)
    # Entities survive tag stripping and then read as text. A language switcher
    # written "English &nbsp;&nbsp;|&nbsp;&nbsp; 简体中文" slipped past the
    # switcher filter for exactly this reason and became a plugin's stated
    # purpose.
    text = html.unescape(text)
    # Emphasis markers are not content and were reaching every consumer as
    # literal asterisks: "**Powerful, customizable AI assistant**".
    text = re.sub(r"\*\*|__(?=\w)|(?<=\w)__", "", text)
    return text


# ------------------------------------------------------------ script and language

CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
LATIN_RE = re.compile(r"[A-Za-z]")

# Above this share of CJK a document is not something the scorer can read at
# all: ranking tokenises on [a-z0-9]+, so Chinese text yields no tokens and the
# entry scores zero against every query -- Chinese ones included. 57 of 752
# plugins sat in that state, reachable only through the shortlist's tier filler.
CJK_DOMINANT = 0.15

# A line is judged by the same measure, because a line is a small document and
# a second threshold would only be a second thing to tune. Getting this wrong
# in either direction is easy to see: at one CJK character per line, "Open the
# menu to find the 美术馆 / ArtGallery entry" reads as Chinese and the plugin
# loses its own instructions; at half, "使用 WebDAV 同步" reads as English and
# Chinese leaks into the purpose. The two populations separate cleanly around
# this value -- English sentences carrying a Chinese proper noun sit at 0.02 to
# 0.13, genuinely bilingual lines at 0.15 and above.
CJK_LINE = CJK_DOMINANT


def cjk_ratio(text):
    """CJK characters as a share of the letters present.

    Punctuation, digits and markup are not evidence of language either way, so
    only letters are counted. A document with no letters at all is not
    non-English; it is empty, and returns zero.
    """
    if not text:
        return 0.0
    han = len(CJK_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    return han / (han + latin) if han + latin else 0.0


# A bilingual repository usually keeps the translation beside the README under
# a name of its own. The search query asks for README.md by name, so these are
# invisible to it -- but the root listing comes back in the same response, so
# recognising one costs nothing. Every spelling found in the catalogue is
# covered: README_en.md, README.en.md and README_EN.md.
ENGLISH_README_RE = re.compile(
    r"^readme[ ._-]?(en|eng|english|en[_-]?us|en[_-]?gb)\.(md|markdown|txt)$",
    re.IGNORECASE)


def english_readme_name(names):
    """The translated README among a repository's root file names, if any."""
    for name in names:
        if ENGLISH_README_RE.match(name):
            return name
    return None


# A Latin-script line is not an English line. In a Chinese README the Latin
# characters are overwhelmingly paths, licence boilerplate, URLs and code, so
# filtering by script alone produces confident nonsense: it gave dither256 the
# purpose "`python tools/compile_mo.py locales/zh_CN.po`" and weread.koplugin
# "Copyright (C) 2026 finlater and contributors." Both are worse than the empty
# purpose they replaced, because a model reading one will describe the plugin
# from it. A line has to read as a sentence to count.
PROSE_WORD_RE = re.compile(r"\b[A-Za-z][a-z]{2,}\b")
NOT_PROSE_RE = re.compile(
    r"^(copyright|licen[cs]e|spdx|©|https?://|"
    r"\S+\.(lua|py|md|zip|json|png|sh|bin|toml|ya?ml)\b)", re.IGNORECASE)
LIST_MARKUP_RE = re.compile(r"^[#>\-*+\d.\s]+")

# Five lowercase words is a sentence rather than a caption, and two such lines
# is a section rather than a stray English sentence inside a Chinese one.
PROSE_WORDS = 5
PROSE_LINES = 2


def _is_prose(line):
    body = LIST_MARKUP_RE.sub("", line.strip())
    if NOT_PROSE_RE.match(body):
        return False
    return len(PROSE_WORD_RE.findall(body)) >= PROSE_WORDS


def english_blocks(readme):
    """The runs of English prose in a bilingual README.

    Bilingual READMEs come in two shapes: a switcher with the whole document
    written twice, and a Chinese document with an English section appended.
    Both split at their CJK lines, so what lies between those lines is the
    candidate set -- and a block survives only if it holds actual sentences.

    Fenced code is dropped rather than carried. It is stripped downstream
    anyway, and splitting a fence at a Chinese comment inside it would leave an
    unbalanced fence that the stripper then fails to remove, spilling the code
    into a plugin's stated purpose.

    The result stays markdown, because the callers are the ordinary purpose and
    feature extractors and they read headings and bullets.
    """
    if not readme:
        return ""
    blocks, current, in_fence = [], [], False
    for raw in readme.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line and cjk_ratio(line) > CJK_LINE:
            blocks.append(current)
            current = []
            continue
        current.append(raw)
    blocks.append(current)

    kept = [b for b in blocks
            if sum(1 for line in b if _is_prose(line)) >= PROSE_LINES]
    return "\n\n".join("\n".join(b).strip() for b in kept)


def english_view(readme, sidecar=""):
    """The text to extract from, when the README itself is not in English.

    Three sources, best first: a translated sidecar README, which is the same
    document and needs no filtering; the English blocks of a bilingual README;
    and, failing both, the original. Returning the original is the honest
    outcome -- a Chinese purpose at least reports what the repository says,
    and no rule can turn a monolingual document into another language.
    """
    if sidecar and cjk_ratio(sidecar) < CJK_DOMINANT:
        return sidecar
    if cjk_ratio(readme) < CJK_DOMINANT:
        return readme
    return english_blocks(readme) or readme


def glossary_keywords(text, glossary, limit=12):
    """English labels for the concepts a Chinese document names.

    Not a translation. The scorer needs something to match on, and a keyword is
    a label rather than a claim -- so this adds words to the match surface and
    never writes a sentence or touches a purpose. A plugin whose README only
    ever says 屏保 becomes findable by "screensaver" while still, honestly,
    having no English description of itself.

    Substring matching, because Chinese is written without spaces and there is
    no boundary to anchor to. Overlapping keys are not a problem: 微信读书 and
    读书 can both fire, and both labels are true.

    Self-limiting on English input -- a document with no CJK in it matches
    nothing here, so this needs no guard at the call site.
    """
    if not text or not glossary:
        return []
    found, seen = [], set()
    for term, english in glossary.items():
        if term not in text:
            continue
        for word in english.split():
            if word not in seen:
                seen.add(word)
                found.append(word)
    return found[:limit]


def readability(purpose, description=""):
    """Whether an English query can reach this entry at all.

    Three outcomes, and the middle one is the reason this function exists.
    `english` is an entry the scorer can read. `silent` says extraction found
    no prose anywhere, which is a fact about the repository. `unreadable` says
    the repository documented itself and the index cannot use it -- the entry
    scores zero against every query, in any language, and nothing about the
    result looks like a failure from the outside.

    That last state went unnoticed for months across 57 plugins because no
    count of it existed. Naming it is what lets the build report it.
    """
    says = (purpose or description or "").strip()
    if not says:
        return "silent"
    if cjk_ratio(says) >= CJK_DOMINANT:
        return "unreadable"
    return "english"


def extract_purpose(readme, limit=320, enough=110):
    """The opening prose, up to a useful length.

    READMEs open with badge rows, logos and title headings far more often than
    with prose, so the first non-empty line is usually the wrong answer.

    Collection does not stop at the first paragraph either. Plenty of READMEs
    open with a single short line -- "Manga reader for KOReader." -- and every
    consumer of this field then has to tell that plugin apart from a dozen
    others on those five words. Keep taking paragraphs until there is enough to
    describe something.
    """
    if not readme:
        return ""
    body = clean_markdown(readme)
    paragraph = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if sum(len(p) for p in paragraph) >= enough:
                break
            continue
        if line.startswith("#") or BADGE_RE.match(line):
            continue
        if line.startswith(("|", ">", "---", "===", "* ", "- ", "+ ")):
            continue
        # Any numbered step, not just the first. Skipping only "1. " let the
        # rest of an install list walk into a plugin's stated purpose:
        # "…displays weather on your sleep screen. 2. Extract the folder 3."
        if re.match(r"^\d+[.)]\s", line):
            continue
        # Language switcher rows -- "English | 中文 | Español" -- sit above the
        # real first paragraph in bilingual READMEs and are not prose. Taking
        # one as a plugin's purpose is worse than having none, because a model
        # reading it will try to describe the plugin from it.
        if "|" in line and all(len(part.strip()) < 20 for part in line.split("|")):
            continue
        paragraph.append(line)
        if sum(len(p) for p in paragraph) > limit:
            break
    if not paragraph:
        return ""
    text = " ".join(paragraph)
    text = re.sub(r"\s+", " ", text).strip()
    # Too short to be a description of anything. An empty purpose is honest and
    # the consumers all handle it; a three-word fragment invites invention.
    if len(text) < 25:
        return ""
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut + "…"
    return text


BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.{6,})$")
EMPHASIS_RE = re.compile(r"(\*\*|__|`)")
SKIP_BULLET = re.compile(
    r"^(install|download|copy|clone|extract|unzip|restart|see |read the|refer|"
    r"licen[cs]e|contribut|credit|thanks|todo|changelog|version|require|"
    r"tested on|screenshot)", re.IGNORECASE)


def extract_features(readme, limit=10, per_item=130, total=760):
    """The feature bullets, which is where a plugin says what it does.

    The opening paragraph is often a slogan -- KOAssistant's is "Powerful,
    customizable AI assistant for KOReader" -- while the capability that
    answers a real question sits in a list further down: "Highlight text ->
    translate, explain, define words". Purpose extraction skips list markup by
    design, so that content reached no consumer at all and the plugin was
    invisible to anyone asking about translation.

    Installation and housekeeping bullets are dropped; they describe the repo,
    not the plugin.
    """
    if not readme:
        return []
    out, seen = [], set()
    for raw in clean_markdown(readme).splitlines():
        match = BULLET_RE.match(raw)
        if not match:
            continue
        item = EMPHASIS_RE.sub("", match.group(1)).strip(" .;:")
        item = re.sub(r"\s+", " ", item)
        if len(item) < 6 or SKIP_BULLET.match(item):
            continue
        if len(item) > per_item:
            item = item[:per_item].rsplit(" ", 1)[0] + "…"
        key = item.lower()[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit or sum(len(x) for x in out) >= total:
            break
    return out


# Sections every README has, about the repository rather than the plugin.
# Feeding them to categorisation reads housekeeping as capability: a weather
# plugin landed in "Dictionary & language" because its README explains how its
# own interface gets translated.
HOUSEKEEPING_HEADING = re.compile(
    r"^(install|usage|configur|setup|getting started|contribut|licen[cs]e|credit|"
    r"thanks|acknowledg|changelog|change log|roadmap|todo|faq|requirement|"
    r"screenshot|author|related|localis|localiz|translat|support|donate|"
    r"disclaimer|table of contents|troubleshoot|development|building|api information|"
    r"how it works)", re.IGNORECASE)

# The same idea, but matched anywhere in a heading rather than at the start:
# "What Gets Translated and How" and "Current Translation Status" are both
# about a plugin's own interface, and neither begins with the giveaway word.
HOUSEKEEPING_ANYWHERE = re.compile(
    r"(translat|localis|localiz|contribut|licen[cs]e|changelog|acknowledg|"
    r"sponsor|donate)", re.IGNORECASE)


def extract_headings(readme, limit=25):
    if not readme:
        return []
    return [h.strip() for h in HEADING_RE.findall(clean_markdown(readme))][:limit]


def feature_headings(headings):
    """Headings that say what the plugin does, housekeeping dropped."""
    return [h for h in headings
            if not HOUSEKEEPING_HEADING.match(h)
            and not HOUSEKEEPING_ANYWHERE.search(h)]


# --------------------------------------------------------------- lua patch text

LUA_HEADER_RE = re.compile(r"^\s*(?:--\[\[(?P<block>.*?)\]\]|(?P<line>(?:\s*--[^\n]*\n)+))", re.DOTALL)


def extract_patch_purpose(source, limit=220):
    """Leading comment block of a patch file.

    KOReader's user-patch convention is to explain the patch at the top of the
    file, which makes this far more reliable than the repository README -- one
    README describes fourteen patches at once and names none of them.
    """
    if not source:
        return ""
    match = LUA_HEADER_RE.match(source)
    if not match:
        return ""
    raw = match.group("block") or match.group("line") or ""
    lines = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or set(line) <= {"-", "=", "*"}:
            continue
        lines.append(line)
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


# ------------------------------------------------------------------- vocabulary

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "your", "you", "this", "that",
    "from", "into", "to", "of", "in", "on", "at", "by", "is", "are", "be", "it",
    "its", "as", "can", "will", "not", "no", "if", "when", "then", "than", "so",
    "koreader", "plugin", "plugins", "patch", "patches", "koplugin", "reader",
    "ereader", "e", "readme", "install", "installation", "usage", "license",
    "contributing", "features", "feature", "screenshot", "screenshots", "how",
    "what", "why", "use", "using", "used", "user", "users", "new", "add", "get",
    "support", "supported", "supports", "version", "release", "releases",
}

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#-]{1,}")


def readme_terms(readme, limit=30):
    """Distinctive words from the README body, by frequency.

    The first paragraph and the headings are not the whole story: a plugin
    explains what it actually does further down, and that text is the reason
    this project fetches READMEs at all. Frequency ordering keeps what the
    document is about and drops the one-off mentions.
    """
    if not readme:
        return []
    counts = {}
    for token in TOKEN_RE.findall(clean_markdown(readme).lower()):
        if token in STOPWORDS or len(token) < 4 or token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, count in ordered if count > 1][:limit]


def condense_readme(readme, max_chars=1200):
    """Cleaned README prose for the opt-in deep search.

    Deduplicated word by word: repetition adds nothing to a match and the
    whole point is to keep 740 of these small enough to ship as one lazily
    loaded file.
    """
    if not readme:
        return ""
    seen, out = set(), []
    for token in TOKEN_RE.findall(clean_markdown(readme).lower()):
        if token in STOPWORDS or len(token) < 3 or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if sum(len(w) + 1 for w in out) >= max_chars:
            break
    return " ".join(out)


def keywords(description, topics, headings, extra=(), limit=24):
    seen = []
    for topic in topics:
        token = topic.lower().strip()
        if token and token not in seen:
            seen.append(token)
    pool = " ".join([description or ""] + list(headings) + list(extra)).lower()
    for token in TOKEN_RE.findall(pool):
        if token in STOPWORDS or len(token) < 3:
            continue
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen[:limit]


# -------------------------------------------------------------------- categories

CATEGORY_LABELS = {
    "sync": "Sync",
    "ui": "Interface",
    "dict": "Dictionary & language",
    "web": "Web & articles",
    "library": "Library & catalogue",
    "ai": "AI",
    "device": "Device specific",
    "files": "Files & transfer",
    "reading": "Reading experience",
    "content": "External content",
    "notes": "Notes & annotation",
    "games": "Games & puzzles",
    "misc": "Other",
}

# Vocabulary widened against the published index, where a third of the
# catalogue landed in `misc`. The gaps were specific, not general: games had no
# category at all, note-taking had none, and compound names like
# "filebrowser" (270 stars) missed a rule written as the bare word "file".
CATEGORY_RULES = {
    # Without "highlight", "annotation" and "bookmark", which describe what is
    # being moved and not the moving. They put 63 plugins that merely show or
    # export a highlight into the sync bucket, and the genuine synchronisers
    # all say sync, webdav or a provider name anyway.
    "sync": ["sync", "synchron", "syncthing", "webdav", "dropbox", "nextcloud",
             "onedrive", "progress sync", "backup", "restore", "cloud sync"],
    # Only the marketing adjectives are gone. "Fully customisable" appears in
    # every other README and claims nothing; "menu" and "button" looked like
    # the same kind of noise and are not -- dropping them cost a start menu
    # and an on-screen button bar their only category.
    "ui": ["ui", "theme", "interface", "homescreen", "home screen", "menu",
           "menus", "overlay", "icon", "font", "fonts", "layout", "skin",
           "minimal", "statusbar", "status bar", "screensaver", "cover",
           "covers", "clock", "shortcut", "shortcuts", "toolbar", "tabbar",
           "tab bar", "widget", "keyboard", "launcher", "button", "redesign",
           "dark mode", "homepage", "home page", "page turn", "animation"],
    "dict": ["dictionary", "dictionaries", "translate", "translation", "anki",
             "vocabulary", "language", "flashcard", "flashcards", "stardict",
             "wordwise", "glossary", "lookup", "look up", "wordreference",
             "pinyin", "ime", "thesaurus", "definition", "define", "spelling",
             "grammar", "furigana", "jisho"],
    "web": ["web", "browser", "rss", "feed", "feeds", "article", "articles",
            "readeck", "wallabag", "pocket", "news", "http", "url", "internet",
            "readability", "bookmarks", "karakeep", "linkding"],
    "library": ["opds", "calibre", "catalog", "catalogue", "zotero", "library",
                "shelf", "shelves", "bookshelf", "metadata", "isbn",
                "goodreads", "audiobookshelf", "collection", "collections",
                "jellyfin", "plex", "kavita", "komga", "hardcover"],
    "ai": ["ai", "llm", "gpt", "chatgpt", "claude", "gemini", "deepseek",
           "ollama", "assistant", "summar", "openai", "anthropic"],
    "device": ["kobo", "kindle", "remarkable", "pocketbook", "boox", "stylus",
               "gamepad", "frontlight", "battery", "screenlock", "screenlockpin",
               "screen lock", "pin", "pin code", "wifi", "bluetooth", "usb",
               "sleep", "airplane", "airplanemode", "hardware"],
    "files": ["file", "files", "filebrowser", "file browser", "filemanager",
              "file manager", "transfer", "wifi transfer", "sideload",
              "localsend", "ftp", "sftp", "smb", "samba", "share", "send",
              "receive", "cloud", "storage", "import", "export", "upload",
              "download"],
    # Comic panels and illustrations moved to `content`, where the plugins
    # reading them belong. Nothing else left: "track", "unit" and "image" look
    # generic and are load-bearing -- a unit converter, a weight tracker and a
    # map viewer each had no other category.
    "reading": ["statistic", "statistics", "stats", "progress", "reading time",
                "typography", "pagination", "tts", "text to speech", "speed",
                "goal", "goals", "xray", "x-ray", "character", "characters",
                "timeline", "unit conversion", "session", "streak", "unit",
                "units", "convert", "conversion", "habit", "tracking", "track",
                "hardcover", "storygraph", "animation", "page turn",
                "page-turn", "image", "images", "planner", "plan", "schedule",
                "tbr"],
    "content": ["manga", "comic", "comics", "panel", "illustration",
                "illustrations", "novel", "novels", "legado",
                "zlibrary", "z-library", "webnovel", "fanfic", "fanfiction",
                "podcast", "audiobook", "audiobooks", "ao3",
                "archive of our own", "weread", "fanqie", "scanlation"],
    # The vocabulary `sync` used to hold. A bookmark ribbon and a highlight
    # exporter are about the marks a reader leaves in a book, which is this
    # category and not the transport that may or may not carry them.
    "notes": ["note", "notes", "notebook", "note-taking", "notetaking",
              "handwritten", "handwriting", "memo", "journal", "scribble",
              "annotate", "annotation", "annotations", "highlight",
              "highlights", "highlighted", "bookmark", "bookmarks", "dogear",
              "dog ear", "dog-ear", "clipping", "clippings", "clipboard",
              "excerpt", "margin"],
    "games": ["game", "games", "puzzle", "puzzles", "sudoku", "solitaire",
              "chess", "wordsearch", "word search", "crossword", "minesweeper",
              "tetris", "arcade", "trivia", "quiz"],
}


def _compile_rules(needles):
    """Whole words only.

    Substring matching looks fine until "ui" fires on "build", "ai" on
    "available" and "pen" on "open" -- every short rule quietly matches most of
    the catalogue. Spaces become flexible whitespace so multi-word rules still
    survive a line break.
    """
    parts = [r"\b" + re.escape(n).replace(r"\ ", r"\s+") + r"\b" for n in needles]
    return re.compile("|".join(parts))


CATEGORY_PATTERNS = {cid: _compile_rules(ns) for cid, ns in CATEGORY_RULES.items()}

# Categories that describe what a plugin *is*, not what its README mentions in
# passing. "Works on Kobo and Kindle" appears in a quarter of all descriptions,
# which made `device` the largest bucket in the catalogue and the chip useless.
# These match only the repository name and topics, where a device name is a
# claim of identity rather than a compatibility note.
IDENTITY_CATEGORIES = {"device"}

# Most identifying first. `categories[0]` is where a plugin is filed -- the
# catalogue's section heading, the first badge on its card -- and until now
# that was decided by the order the rules happened to be declared in. `sync`
# was declared first and `notes` eleventh, so every plugin that both syncs and
# annotates filed as sync: the catalogue printed 104 plugins under Sync and 8
# under Notes, with icon changers and an art gallery among the synchronisers.
#
# The order is by how much the label claims. "AI" or "Games" says what a plugin
# *is*; "Interface" and "Files" describe a surface most plugins touch on their
# way to doing something else, so they file a plugin only when nothing more
# specific applies. Fixed rather than computed from the nightly counts, so a
# plugin does not change section because two categories swapped sizes.
PRIMARY_ORDER = ("ai", "games", "content", "dict", "library", "sync", "notes",
                 "web", "device", "reading", "files", "ui", "misc")

# Measured after the first build shipped: ordering was the whole fix. Narrowing
# the broad rules on top of it looked like the same repair and was not -- it
# left 33 plugins with no category at all and took `misc` from 20% to 24%,
# while the sections it was supposed to improve were already flat. A category
# that describes a surface is harmless once it can only file a plugin that
# nothing more specific claims, so breadth costs nothing here and coverage is
# worth keeping. The two cuts that stayed are the ones with a misfiling behind
# them, not a hunch.


def categorise(text_pool, topics, name=""):
    """Rule-based, multi-label. `misc` only when nothing matched.

    A large misc bucket means these rules need work; it never means the
    taxonomy was wrong. Track it in the build summary.
    """
    topics = list(topics or [])
    # Punctuation becomes whitespace first. Regex treats "_" as a word
    # character, so `\bpinyin\b` never fires on "pinyin_enhancement" -- and
    # repository names are full of underscores and dots.
    def flatten(*parts):
        return re.sub(r"[^a-z0-9]+", " ", " ".join(p or "" for p in parts).lower())

    broad = flatten(text_pool, name, *topics)
    identity = flatten(name, *topics)
    found = [
        cid for cid, pattern in CATEGORY_PATTERNS.items()
        if pattern.search(identity if cid in IDENTITY_CATEGORIES else broad)
    ]
    found.sort(key=lambda cid: PRIMARY_ORDER.index(cid)
               if cid in PRIMARY_ORDER else len(PRIMARY_ORDER))
    return found or ["misc"]


# ------------------------------------------------------------------------ tiers

DORMANT_DAYS = 365
STUB_README_BYTES = 300

# Silence is not the same as abandonment. A crossword game, a crash-log viewer
# or a Syncthing launcher can be finished: it does one thing, it works, and
# there is nothing left to commit. Demoting on the calendar alone hid 31
# plugins that had nothing else wrong with them, several with real followings.
#
# Two things rescue such a plugin: people use it, and it is not so old that
# KOReader has moved out from under it. Past three years that second point
# stops holding regardless of stars -- the API has changed too much to assume
# anything still runs.
STALE_DAYS = 1095
ADOPTED_STARS = 10

# Being a fork is not a quality signal in this ecosystem: plugins routinely
# start as a fork of a template or of another plugin, and three of the most
# popular ones in the catalogue are forks. What is noise is an *unloved* fork --
# a copy nobody starred. That is the distinction the AppStore page already
# draws with its "include zero-star forks" toggle.
FORK_MIN_STARS = 1


def _age_days(iso_timestamp, now=None):
    if not iso_timestamp:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = datetime.datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (now - stamp).days


def activity_of(iso_timestamp, archived=False, now=None):
    if archived:
        return "archived"
    age = _age_days(iso_timestamp, now)
    if age is None:
        return "dormant"
    return "active" if age <= DORMANT_DAYS else "dormant"


PATCH_FILE_RE = re.compile(r"^(\d+)-(.+)\.lua$", re.IGNORECASE)


def parse_patch_name(filename):
    """Split KOReader's `N-name.lua` convention.

    The numeric prefix is the load order, not decoration, so it is returned
    rather than stripped and forgotten.
    """
    match = PATCH_FILE_RE.match(filename)
    if not match:
        return None, None
    order = int(match.group(1))
    label = match.group(2).replace("-", " ").replace("_", " ").strip()
    return order, label


def tier_of_patch(entry, curated_tier=None):
    """Patch tiers weigh different things than plugin tiers.

    A patch monkey-patches KOReader core. When a plugin goes stale a feature
    stops working; when a patch goes stale against a newer KOReader it can stop
    the device booting. So freshness here is measured on the file itself, and
    an undocumented patch -- no comment saying what it does -- is treated as
    unsafe to recommend rather than merely thin.
    """
    reasons = []
    if entry.get("purpose"):
        reasons.append("has_header")
    else:
        reasons.append("no_header")
    if entry.get("archived"):
        reasons.append("archived")
    reasons.append(entry.get("activity", "dormant"))

    if curated_tier:
        return curated_tier, ["curated"] + reasons

    demoted = (
        not entry.get("purpose")
        or entry.get("archived")
        or entry.get("activity") != "active"
    )
    return ("C" if demoted else "B"), reasons


def tier_of(entry, curated_tier=None):
    """A curated, B automatically clean, C shown only on request.

    Writing a KOReader plugin became easy, so the catalogue grows faster than
    the useful part of it does. Past a certain size the job is not listing
    things but excluding them, and every signal used here is free.
    """
    reasons = []
    unloved_fork = entry.get("is_fork") and entry.get("stars", 0) < FORK_MIN_STARS

    dormant = entry.get("activity") != "active"
    age = _age_days(entry.get("pushed_at"))
    settled = (
        dormant
        and not entry.get("archived")
        and entry.get("stars", 0) >= ADOPTED_STARS
        and entry.get("readme_bytes", 0) >= STUB_README_BYTES
        and entry.get("has_meta")
        and (age is None or age <= STALE_DAYS)
    )

    if entry.get("has_meta"):
        reasons.append("has_meta")
    else:
        reasons.append("no_meta")
    if unloved_fork:
        reasons.append("fork")
    if entry.get("archived"):
        reasons.append("archived")
    reasons.append("settled" if settled else entry.get("activity", "dormant"))
    if entry.get("readme_bytes", 0) >= STUB_README_BYTES:
        reasons.append("documented")
    else:
        reasons.append("stub")

    if curated_tier:
        return curated_tier, ["curated"] + reasons

    demoted = (
        not entry.get("has_meta")
        or unloved_fork
        or entry.get("archived")
        or (dormant and not settled)
        or entry.get("readme_bytes", 0) < STUB_README_BYTES
    )
    return ("C" if demoted else "B"), reasons
