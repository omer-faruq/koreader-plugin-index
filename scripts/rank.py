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

# Names run their words together. A third of the catalogue is called something
# like `readingstyle.koplugin` -- one indexed word, not two -- so the prefix
# rule that works for prose cannot see inside them, and the plugin scored zero
# against its own title. Substring matching is too loose for prose fields; on a
# name it is the only way in, and the name is the weakest weight, so a
# coincidence surfaces an entry without ever outranking one that matched on
# what it does.
MIN_NAME_SUBSTRING = 4

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

# Awarded once, on top of the fields, when the query accounts for essentially
# the whole of a plugin's own name. This is deliberately not a raise of
# WEIGHT_NAME: "a query word appears somewhere in the name" stays weak evidence
# -- that is what the manga failure above was -- while "the query *is* the name"
# is close to certain intent, and someone typing a name they already know has to
# be answered first or they conclude the catalogue does not have it.
TITLE_BONUS = 6.0

# The query has to account for the name, *and* the name has to account for the
# query. One direction alone is not enough: "customise the SimpleUI homescreen"
# names SimpleUI in full, but it is a description of a need rather than a
# lookup, and rewarding the name there put five near-identical forks of the
# plugin above the extension that actually answers it. Someone looking a name
# up types the name and little else.
TITLE_MIN_COVERAGE = 0.8
TITLE_MIN_FOCUS = 0.5

# Guards on the measure. A two-letter word tiles too many names by accident,
# and a very short name is covered by almost anything.
MIN_TITLE_WORD = 3
MIN_TITLE_SLUG = 5

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


# GitHub allows every shape at once: `annotationsync`, `AnnotationSync`,
# `annotation-sync`, all under a `.koplugin` suffix, and patches under a `2-`
# load-order prefix. Splitting a name into words is what lets a query reach it
# at all; dropping the boilerplate is what keeps `koreader-menu-customizer`
# from reading as a name that is one third scaffolding when coverage is
# measured over it below. Written as a pair of groups rather than as a
# lookbehind so that this and the page's copy are the same expression: an older
# mobile browser cannot parse a lookbehind, and failing to parse one there
# takes the whole page down rather than one search.
CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
NAME_NOISE = {"koreader", "koplugin", "plugin", "ko", "lua"}


def name_of(entry):
    """The name the result card shows.

    A patch is displayed under its filename, not under the repository that
    happens to hold it, so that is what a search for it has to match. Scoring
    the repository instead meant a patch could not be found by the only name
    the page ever showed for it.
    """
    return entry.get("path") or entry.get("repo", "")


def name_words(name):
    spaced = CAMEL_BOUNDARY.sub(r" ", name or "")
    return [
        word
        for word in WORD_RE.findall(spaced.lower())
        if word not in NAME_NOISE and not word.isdigit()
    ]


def query_words(text):
    """Every word in the query, stopwords kept.

    The stopword list exists because this catalogue is entirely about reading:
    `read`, `reading`, `book` carry no signal in prose. They carry plenty in a
    name -- half these plugins are called one of them -- so the title measure
    gets the query before it is filtered, and only the title measure does.
    """
    return WORD_RE.findall((text or "").lower())


def title_match(entry, tokens, raw_words):
    """Is the query, in substance, this plugin's own name?

    Coverage asks how much of the name the query accounts for. It is measured
    over the name rather than over the query, so padding cannot dilute it --
    "the reading style plugin please" covers `readingstyle` exactly as fully as
    "reading style" does -- and over characters rather than words, because the
    name is usually one run-together word, which is the whole problem this is
    here to solve. The direction matters for the manga failure: `manga` covers
    five of the fourteen characters of `mangapanelzoom`, which is not a title
    match and does not become one.

    Focus asks the reverse: how much of the query the name accounts for. A
    query that names a plugin and then asks for something else is a description
    of a need, and the fields already know how to rank those.

    Coverage reads the query unfiltered, because half of these names are made
    of stopwords -- `reading`, `book`, `reader` -- and dropping those is what
    made a plugin invisible under its own title. Focus reads the filtered
    tokens, because it is asking what the query was *about*.
    """
    slug = "".join(name_words(name_of(entry)))
    if len(slug) < MIN_TITLE_SLUG:
        return False

    covered = [False] * len(slug)
    for word in raw_words:
        if len(word) < MIN_TITLE_WORD:
            continue
        at = slug.find(word)
        while at != -1:
            for i in range(at, at + len(word)):
                covered[i] = True
            at = slug.find(word, at + 1)
    if sum(covered) / len(slug) < TITLE_MIN_COVERAGE:
        return False

    # No tokens at all means the query was nothing but stopwords, which the
    # coverage test just found spelled out the name: `book reader` against
    # `bookreader` is all name and nothing else.
    if not tokens:
        return True
    inside = sum(1 for token in tokens if token in slug)
    return inside / len(tokens) >= TITLE_MIN_FOCUS


def _wordset(text):
    words = set(WORD_RE.findall((text or "").lower()))
    return words, {stem(w) for w in words}


def _matches(token, pool, substring=False):
    words, stems = pool
    if token in words or stem(token) in stems:
        return True
    if len(token) < MIN_PREFIX:
        return False
    for word in words:
        if word.startswith(token) or (len(word) >= MIN_INDEX_PREFIX and token.startswith(word)):
            return True
        if substring and len(token) >= MIN_NAME_SUBSTRING and token in word:
            return True
    return False


def _hits(tokens, haystack, substring=False):
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
            or (substring and len(token) >= MIN_NAME_SUBSTRING and token in word)
            for word in words
        ):
            count += 1
    return count


def fields_of(entry):
    """(weight, text, substring-allowed) per field."""
    fields = [
        (WEIGHT_KEYWORD, " ".join(entry.get("keywords", [])), False),
        (WEIGHT_PURPOSE, entry.get("purpose", ""), False),
        (WEIGHT_DESCRIPTION, entry.get("description", ""), False),
        (WEIGHT_CATEGORY, " ".join(entry.get("categories", [])), False),
        (WEIGHT_NAME, " ".join(name_words(name_of(entry))), True),
    ]
    # Present only when the caller has loaded readme-index.json and attached
    # it. Deep search is opt-in, so index.json never carries this.
    if entry.get("readme"):
        fields.append((WEIGHT_README, entry["readme"], False))
    return fields


def score(entry, tokens, raw_words=()):
    """Each query token counts once, scored by the strongest field it matched.

    Summing every field instead counts one word three times over, because the
    fields overlap by construction: keywords are extracted from the description,
    and the purpose restates it in prose. On real data that inflated whatever
    happened to carry the most text -- a panel-zoom plugin outscored the actual
    manga reader 20.7 to 14.7 purely by repeating two words across three fields.
    """
    title = TITLE_BONUS if title_match(entry, tokens, raw_words) else 0.0
    if not tokens and not title:
        return 0.0

    pools = [(weight, _wordset(text), sub) for weight, text, sub in fields_of(entry)]
    total = 0.0
    for token in tokens:
        best = 0.0
        for weight, pool, sub in pools:
            if weight > best and _matches(token, pool, sub):
                best = weight
        total += best

    # A name made entirely of stopwords -- `bookreader`, `readinglist` -- leaves
    # nothing for the fields to score, and the title match is then the only
    # evidence there is. It is enough on its own; nothing else is.
    if total <= 0 and not title:
        return 0.0
    total += title

    total += TIER_BONUS.get(entry.get("tier", "C"), 0.0)
    # With double counting gone, popularity is the main separator between two
    # plugins that match a query equally well -- and for "which should I
    # install", it is a real signal. It still cannot carry an entry that
    # matched nothing.
    total += POPULARITY_WEIGHT * math.log10(max(entry.get("stars", 0), 0) + 1)
    return total


def rank(entries, query, limit=10):
    tokens = tokenise(query)
    raw = query_words(query)
    scored = [(score(e, tokens, raw), e) for e in entries]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"].lower()))
    return [e for _, e in scored[:limit]]


def explain(entry, query):
    """Why this matched, for the result card. Never invent a reason."""
    tokens = tokenise(query)
    matched = [k for k in entry.get("keywords", []) if k in tokens]
    if not matched:
        matched = [t for t in tokens if t in (entry.get("purpose", "") or "").lower()]
    matched = matched[:5]
    # A title match is often the only reason an entry is here, and the card has
    # to say so. A plugin found under its own name can carry a description in a
    # language the reader does not have; a top result listing no reason at all
    # reads as noise rather than as the answer to what was typed.
    if title_match(entry, tokens, query_words(query)):
        return (["its name"] + matched)[:5]
    return matched
