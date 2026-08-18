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
    # "menu", "button" and "customizable" are gone: every plugin adds a menu
    # entry and says it is customisable, so they described the surface any
    # plugin touches rather than one that is about the interface.
    "ui": ["ui", "theme", "interface", "homescreen", "home screen", "overlay",
           "icon", "font", "fonts", "layout", "skin", "minimal", "statusbar",
           "status bar", "screensaver", "cover", "covers", "clock", "shortcut",
           "shortcuts", "toolbar", "tabbar", "tab bar", "widget", "keyboard",
           "launcher", "redesign", "dark mode", "homepage", "home page",
           "page turn", "animation"],
    "dict": ["dictionary", "dictionaries", "translate", "translation", "anki",
             "vocabulary", "language", "flashcard", "flashcards", "stardict",
             "wordwise", "glossary", "lookup", "look up", "wordreference",
             "pinyin", "ime", "thesaurus", "definition", "define", "spelling",
             "grammar", "furigana", "jisho"],
    "web": ["web", "browser", "rss", "feed", "feeds", "article", "articles",
            "readeck", "wallabag", "pocket", "news", "readability",
            "karakeep", "linkding"],
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
    # Verbs of moving data -- download, upload, share, send -- are what every
    # plugin with a network connection does. What identifies this category is
    # the file itself and the protocol carrying it.
    "files": ["file", "files", "filebrowser", "file browser", "filemanager",
              "file manager", "transfer", "wifi transfer", "sideload",
              "localsend", "ftp", "sftp", "smb", "samba", "cloud", "storage"],
    # "track", "convert", "image" and "plan" are generic verbs and nouns that
    # matched a third of the catalogue. Comic panels and illustrations moved to
    # `content`, which is where the plugins reading them belong.
    "reading": ["statistic", "statistics", "stats", "progress", "reading time",
                "typography", "pagination", "tts", "text to speech", "speed",
                "goal", "goals", "xray", "x-ray", "character", "characters",
                "timeline", "unit conversion", "session", "streak", "habit",
                "hardcover", "storygraph", "animation", "page turn",
                "page-turn", "planner", "tbr"],
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
