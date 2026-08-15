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
    "misc": "Other",
}

CATEGORY_RULES = {
    "sync": ["sync", "synchron", "highlight", "annotation", "bookmark", "webdav",
             "dropbox", "nextcloud", "progress sync", "backup"],
    "ui": ["ui", "theme", "interface", "homescreen", "home screen", "menu",
           "overlay", "icon", "font", "layout", "skin", "minimal", "statusbar",
           "status bar", "screensaver", "cover"],
    "dict": ["dictionary", "translate", "translation", "anki", "vocabulary",
             "language", "flashcard", "stardict", "wordwise", "glossary"],
    "web": ["web", "browser", "rss", "feed", "article", "readeck", "wallabag",
            "pocket", "news", "http", "url", "internet", "readability"],
    "library": ["opds", "calibre", "catalog", "catalogue", "zotero", "library",
                "shelf", "bookshelf", "metadata", "isbn", "goodreads"],
    "ai": ["ai", "llm", "gpt", "chatgpt", "claude", "gemini", "deepseek",
           "ollama", "assistant", "summar", "openai"],
    "device": ["kobo", "kindle", "android", "remarkable", "pocketbook", "stylus",
               "pen", "hardware", "battery", "frontlight", "gesture"],
    "files": ["file", "transfer", "localsend", "ftp", "smb", "share", "send",
              "receive", "cloud", "storage", "import", "export"],
    "reading": ["statistic", "stats", "progress", "reading time", "typography",
                "pagination", "tts", "text to speech", "speed", "goal"],
    "content": ["manga", "comic", "novel", "legado", "zlibrary", "z-library",
                "webnovel", "fanfic", "podcast", "download"],
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


def categorise(text_pool, topics):
    """Rule-based, multi-label. `misc` only when nothing matched.

    A large misc bucket means these rules need work; it never means the
    taxonomy was wrong. Track it in the build summary.
    """
    haystack = " ".join([text_pool or ""] + list(topics)).lower()
    found = [cid for cid, pattern in CATEGORY_PATTERNS.items() if pattern.search(haystack)]
    return found or ["misc"]


# ------------------------------------------------------------------------ tiers

DORMANT_DAYS = 365
STUB_README_BYTES = 300


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

    if entry.get("has_meta"):
        reasons.append("has_meta")
    else:
        reasons.append("no_meta")
    if entry.get("is_fork"):
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
        or entry.get("is_fork")
        or entry.get("archived")
        or entry.get("activity") != "active"
        or entry.get("readme_bytes", 0) < STUB_README_BYTES
    )
    return ("C" if demoted else "B"), reasons
