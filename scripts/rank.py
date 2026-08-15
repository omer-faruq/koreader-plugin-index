"""Reference ranking. The search page must implement these same rules.

Keeping the scorer here rather than only in the page means quality is
measurable: tests/queries.toml holds known question/answer pairs, and the build
fails when a known answer falls out of the top three. Ranking then gets tuned
against measurements instead of intuition, and a regression shows up as a red
build rather than as recommendations that quietly got worse.

If the page and this file drift apart, the tests stop describing what users
actually see -- so any change to the weights below belongs in both.
"""

import math
import re

TOKEN_RE = re.compile(r"[a-z0-9]+")
WORD_RE = re.compile(r"[a-z0-9]+")

# Below this length a prefix match is more likely to be a coincidence than a
# word form, so short query tokens must match exactly.
MIN_PREFIX = 4

# The reverse direction -- an indexed word that prefixes a longer query token,
# "web" against "websites" -- gets a lower floor. Indexed words are curated or
# extracted terms rather than free typing, so a short one is far more likely to
# be a real term than a fragment.
MIN_INDEX_PREFIX = 3

# The repository name is deliberately the *weakest* signal. It was the
# strongest in the first version, and on real data that produced exactly the
# failure the original knowledge base warns about in rule 6: do not assume a
# plugin does something merely because its name suggests it. Searching for
# manga surfaced three plugins with "manga" in the name over the actual manga
# reader, whose name says nothing about manga.
WEIGHT_KEYWORD = 3.5
WEIGHT_PURPOSE = 3.0
WEIGHT_DESCRIPTION = 2.5
WEIGHT_CATEGORY = 2.0
WEIGHT_NAME = 1.5

# Tier is a ranking signal, not just a badge. Curated entries should surface
# above automatically-clean ones, and the long tail of forks, stubs and dormant
# repositories should stay out of the way unless it is all there is.
TIER_BONUS = {"A": 3.0, "B": 1.0, "C": -2.5}

QUERY_STOPWORDS = {
    "i", "want", "to", "a", "an", "the", "my", "me", "for", "with", "on", "in",
    "of", "and", "or", "how", "do", "can", "is", "it", "that", "this", "which",
    "koreader", "plugin", "plugins", "please", "need", "looking", "some",
    # Everything in this catalogue is about reading on an e-reader, so these
    # carry no signal and actively mislead: "read" pulls in Readeck, reading
    # lists and every README that says "read".
    "read", "reads", "reading", "reader", "ereader", "book", "books",
    # Words people pad a request with. "AI help" was matching a plugin that
    # reminds you to rest your eyes, on the strength of "help" alone.
    "help", "best", "good", "better", "something", "anything", "any", "while",
}

# Suffixes stripped to a common stem, longest first. Without this "translate"
# never reaches a plugin whose keyword is "translation", which is how the
# assistant plugin lost its own translation query on real data.
SUFFIXES = ("ings", "ions", "ies", "ing", "ion", "ers", "es", "ed", "er", "ly", "s", "e")
MIN_STEM = 4


def stem(word):
    """Strip suffixes to a fixed point.

    Applied repeatedly rather than once, because a single pass leaves
    "wirelessly" at "wireless" while "wireless" itself reduces further -- and
    two spellings of one word must land on the same stem or the match is lost.
    """
    for _ in range(3):
        for suffix in SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word


def tokenise(text):
    return [t for t in TOKEN_RE.findall((text or "").lower())
            if t not in QUERY_STOPWORDS and len(t) > 1]


def _hits(tokens, haystack):
    """Count query tokens present as words, allowing a light prefix match.

    Substring matching is wrong here for the same reason it was wrong for
    categories -- it fires on fragments. But exact matching is too strict for
    natural language: someone asking about "websites" should still find the
    plugin whose keyword is "web". A prefix match in either direction, floored
    at four characters, covers the common word forms without pretending to be
    a stemmer.
    """
    if not haystack:
        return 0
    words = set(WORD_RE.findall(haystack.lower()))
    stems = {stem(w) for w in words}
    count = 0
    for token in tokens:
        if token in words or stem(token) in stems:
            count += 1
        elif len(token) >= MIN_PREFIX and any(
            word.startswith(token)
            or (len(word) >= MIN_INDEX_PREFIX and token.startswith(word))
            for word in words
        ):
            count += 1
    return count


def score(entry, tokens):
    if not tokens:
        return 0.0
    total = 0.0
    total += WEIGHT_NAME * _hits(tokens, entry.get("repo", ""))
    total += WEIGHT_KEYWORD * _hits(tokens, " ".join(entry.get("keywords", [])))
    total += WEIGHT_PURPOSE * _hits(tokens, entry.get("purpose", ""))
    total += WEIGHT_DESCRIPTION * _hits(tokens, entry.get("description", ""))
    total += WEIGHT_CATEGORY * _hits(tokens, " ".join(entry.get("categories", [])))

    if total <= 0:
        return 0.0

    total += TIER_BONUS.get(entry.get("tier", "C"), 0.0)
    # Popularity breaks ties; it never carries an otherwise irrelevant entry.
    total += math.log10(max(entry.get("stars", 0), 0) + 1)
    return total


def rank(entries, query, limit=10):
    tokens = tokenise(query)
    scored = [(score(e, tokens), e) for e in entries]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"].lower()))
    return [e for _, e in scored[:limit]]


def explain(entry, query):
    """Why this matched, for the result card. Never invent a reason."""
    tokens = set(tokenise(query))
    matched = [k for k in entry.get("keywords", []) if k in tokens]
    if not matched:
        matched = [t for t in tokens if t in (entry.get("purpose", "") or "").lower()]
    return matched[:5]
