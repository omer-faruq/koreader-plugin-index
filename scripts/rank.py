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

# Opt-in deep search. README bodies are noisy -- install instructions, licence
# boilerplate, credits, long lists of unrelated tools -- so a match there is
# the weakest evidence there is. It should surface something when nothing else
# matched, never outrank a plugin whose own keywords answer the question.
WEIGHT_README = 1.0

# Tier is a ranking signal, not just a badge. Curated entries should surface
# above automatically-clean ones, and the long tail of forks, stubs and dormant
# repositories should stay out of the way unless it is all there is.
TIER_BONUS = {"A": 3.0, "B": 1.0, "C": -2.5}

POPULARITY_WEIGHT = 1.5

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
    "without", "across", "between", "just", "also", "there",
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
    """Distinct tokens, in order of first appearance.

    Scoring adds one contribution per token, so a repeated word multiplied its
    own weight. That was harmless while a query was something a person typed,
    and became a real distortion once stage one started expanding questions:
    "web search, internet search, search web, online search" is one idea and
    five copies of the word `search`, and whatever the model happened to repeat
    would pull the ranking after it.
    """
    seen, out = set(), []
    for token in TOKEN_RE.findall((text or "").lower()):
        if token in QUERY_STOPWORDS or len(token) <= 1 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _wordset(text):
    words = set(WORD_RE.findall((text or "").lower()))
    return words, {stem(w) for w in words}


def _matches(token, pool):
    words, stems = pool
    if token in words or stem(token) in stems:
        return True
    if len(token) < MIN_PREFIX:
        return False
    return any(
        word.startswith(token) or (len(word) >= MIN_INDEX_PREFIX and token.startswith(word))
        for word in words
    )


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


def fields_of(entry):
    fields = [
        (WEIGHT_KEYWORD, " ".join(entry.get("keywords", []))),
        (WEIGHT_PURPOSE, entry.get("purpose", "")),
        (WEIGHT_DESCRIPTION, entry.get("description", "")),
        (WEIGHT_CATEGORY, " ".join(entry.get("categories", []))),
        (WEIGHT_NAME, entry.get("repo", "")),
    ]
    # Present only when the caller has loaded readme-index.json and attached
    # it. Deep search is opt-in, so index.json never carries this.
    if entry.get("readme"):
        fields.append((WEIGHT_README, entry["readme"]))
    return fields


def score(entry, tokens):
    """Each query token counts once, scored by the strongest field it matched.

    Summing every field instead counts one word three times over, because the
    fields overlap by construction: keywords are extracted from the description,
    and the purpose restates it in prose. On real data that inflated whatever
    happened to carry the most text -- a panel-zoom plugin outscored the actual
    manga reader 20.7 to 14.7 purely by repeating two words across three fields.
    """
    if not tokens:
        return 0.0

    pools = [(weight, _wordset(text)) for weight, text in fields_of(entry)]
    total = 0.0
    for token in tokens:
        best = 0.0
        for weight, pool in pools:
            if weight > best and _matches(token, pool):
                best = weight
        total += best

    if total <= 0:
        return 0.0

    total += TIER_BONUS.get(entry.get("tier", "C"), 0.0)
    # With double counting gone, popularity is the main separator between two
    # plugins that match a query equally well -- and for "which should I
    # install", it is a real signal. It still cannot carry an entry that
    # matched nothing.
    total += POPULARITY_WEIGHT * math.log10(max(entry.get("stars", 0), 0) + 1)
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
