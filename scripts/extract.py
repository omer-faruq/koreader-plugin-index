"""Deterministic extraction: README text in, structured fields out.

No model runs here. Every field is derived by rule, which is what keeps the
weekly rebuild free, reproducible and free of an API key that would eventually
expire. Where a rule cannot produce something honest it produces nothing --
an empty purpose is correct, an invented one is not.
"""

import datetime
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
    return text


def extract_purpose(readme, limit=220):
    """First paragraph that actually says something.

    READMEs open with badge rows, logos and title headings far more often than
    with prose, so the first non-empty line is usually the wrong answer.
    """
    if not readme:
        return ""
    body = clean_markdown(readme)
    paragraph = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if paragraph:
                break
            continue
        if line.startswith("#") or BADGE_RE.match(line):
            continue
        if line.startswith(("|", ">", "---", "===", "* ", "- ", "1. ")):
            continue
        paragraph.append(line)
        if sum(len(p) for p in paragraph) > limit:
            break
    if not paragraph:
        return ""
    text = " ".join(paragraph)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut + "…"
    return text


def extract_headings(readme, limit=25):
    if not readme:
        return []
    return [h.strip() for h in HEADING_RE.findall(clean_markdown(readme))][:limit]


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
    "sync": ["sync", "synchron", "syncthing", "highlight", "annotation",
             "bookmark", "webdav", "dropbox", "nextcloud", "onedrive",
             "progress sync", "backup", "restore", "cloud sync"],
    "ui": ["ui", "theme", "interface", "homescreen", "home screen", "menu",
           "menus", "overlay", "icon", "font", "fonts", "layout", "skin",
           "minimal", "statusbar", "status bar", "screensaver", "cover",
           "covers", "clock", "shortcut", "shortcuts", "toolbar", "widget",
           "keyboard", "launcher", "button", "redesign", "customize",
           "customise", "customization", "customisation", "dark mode",
           "homepage", "home page", "page turn", "animation"],
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
               "frontlight", "battery", "screenlock", "screenlockpin",
               "screen lock", "pin", "pin code", "wifi", "bluetooth", "usb",
               "sleep", "airplane", "airplanemode", "hardware"],
    "files": ["file", "files", "filebrowser", "file browser", "filemanager",
              "file manager", "transfer", "localsend", "ftp", "sftp", "smb",
              "samba", "share", "send", "receive", "cloud", "storage",
              "import", "export", "upload", "download"],
    "reading": ["statistic", "statistics", "stats", "progress", "reading time",
                "typography", "pagination", "tts", "text to speech", "speed",
                "goal", "goals", "xray", "x-ray", "character", "characters",
                "timeline", "unit conversion", "session", "streak", "unit",
                "units", "convert", "conversion", "habit", "tracking", "track",
                "hardcover", "storygraph", "animation", "page turn",
                "page-turn", "panel", "illustration", "illustrations", "image",
                "images", "planner", "plan", "schedule", "tbr"],
    "content": ["manga", "comic", "comics", "novel", "novels", "legado",
                "zlibrary", "z-library", "webnovel", "fanfic", "fanfiction",
                "podcast", "audiobook", "audiobooks", "ao3",
                "archive of our own", "weread", "fanqie", "scanlation"],
    "notes": ["note", "notes", "notebook", "note-taking", "notetaking",
              "handwritten", "handwriting", "memo", "journal", "scribble",
              "annotate", "annotations", "highlights", "margin"],
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
    return found or ["misc"]


# ------------------------------------------------------------------------ tiers

DORMANT_DAYS = 365
STUB_README_BYTES = 300

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


def tier_of(entry, curated_tier=None):
    """A curated, B automatically clean, C shown only on request.

    Writing a KOReader plugin became easy, so the catalogue grows faster than
    the useful part of it does. Past a certain size the job is not listing
    things but excluding them, and every signal used here is free.
    """
    reasons = []
    unloved_fork = entry.get("is_fork") and entry.get("stars", 0) < FORK_MIN_STARS

    if entry.get("has_meta"):
        reasons.append("has_meta")
    else:
        reasons.append("no_meta")
    if unloved_fork:
        reasons.append("fork")
    if entry.get("archived"):
        reasons.append("archived")
    reasons.append(entry.get("activity", "dormant"))
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
        or entry.get("activity") != "active"
        or entry.get("readme_bytes", 0) < STUB_README_BYTES
    )
    return ("C" if demoted else "B"), reasons
