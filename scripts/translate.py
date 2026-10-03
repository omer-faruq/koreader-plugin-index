"""Machine translation for READMEs no English query can read.

The one place in the pipeline where a model runs, and fenced accordingly. A
repository that documents itself only in Chinese or Russian scores zero against
every English query, one written only in French or German next to nothing, and
the rules in extract.py can do nothing about either --
no rule turns a monolingual document into another language. The glossary adds
labels; this adds the document.

What keeps it honest is what it is allowed to touch. It translates what the
author wrote and nothing else, it is only ever asked when the repository offers
no English of its own, and everything built from it is marked as a machine
translation wherever it is shown. The day the repository publishes a README_en,
the translation stops being used.

DeepSeek, on a prepaid balance kept in the TRANSLATE_API_KEY secret. This was
built on GitHub Models first, for the sake of needing no key at all; that
service was retired on 30 July 2026, and every provider left needs one. The
prepaid balance is the reason for this one: it is a spending cap that cannot
be misconfigured, since there is no card behind it to charge. It is also
strong on Chinese, which is most of what arrives here, and its terms assign
the output to the caller with no attribution asked. Any OpenAI-compatible
endpoint works through TRANSLATE_ENDPOINT and TRANSLATE_MODEL. Standard
library only, like github.py.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

import extract

ENDPOINT = os.environ.get("TRANSLATE_ENDPOINT", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("TRANSLATE_MODEL", "deepseek-flash")

# Bumped when the prompt or the source preparation changes enough that every
# stored translation should be redone. A model change alone does not bump it:
# the old translation is still a translation of the same text.
PROMPT_VERSION = 1

# The same length the README panel shows, so a translated excerpt covers what
# an untranslated one would. Chinese runs close to a token a character and
# English comes back at roughly one and a half times that, which MAX_TOKENS
# leaves room for.
MAX_SOURCE_CHARS = 4000
MAX_TOKENS = 8192

SYSTEM_PROMPT = (
    "You translate README files of KOReader plugins into English.\n"
    "Translate faithfully and completely. Do not summarise, explain, add or "
    "omit anything. Keep the Markdown structure: headings stay headings, list "
    "items stay list items, one output line per input line. Leave code, "
    "commands, file names, paths, URLs, version numbers and product names as "
    "they are. Text that is already English stays as it is. Output only the "
    "translation."
)


class Refused(ValueError):
    """The service answered, and the answer is not a translation of the text.

    Kept apart from a failed request because it will happen again: the same
    text at temperature zero gets the same answer, so the caller remembers it
    rather than asking every night.
    """


class Unavailable(Exception):
    """Translation cannot go on this run: no permission, or no quota left.

    Raised rather than retried. Neither clears in the minutes a build has, and
    the next run asks again for whatever this one did not get to.
    """


def source_text(readme, limit=MAX_SOURCE_CHARS):
    """What gets sent: the prose of the README, from the top, cut on a line.

    Cleaned first, because badges, image links, HTML and code fences cost
    tokens and carry nothing to translate. Returns the text and whether it was
    cut, so the page can say it is showing the opening rather than the whole.
    """
    text = extract.clean_markdown(readme or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip(), True


def source_key(text):
    """Identifies a translation: the exact text sent and the prompt it was
    sent with. Any change to the README's opening asks again; a change
    further down, past what is sent, does not."""
    digest = hashlib.sha1(f"{PROMPT_VERSION}\n{text}".encode("utf-8"))
    return digest.hexdigest()


def acceptable(source, translated):
    """Whether what came back can stand in for the README.

    Still in the original script means the model echoed or refused; far
    shorter than the source means it summarised. Either would be published
    as the plugin's own words in translation, so either is refused.
    """
    if not translated or extract.foreign_script(translated):
        return False
    words = len(re.findall(r"[A-Za-z]+", translated))
    return words >= 8 and len(translated) >= len(source) * 0.4


def translate(text, token, model=MODEL, endpoint=ENDPOINT, timeout=240):
    """English for `text`.

    Refused when the answer is not a translation, Unavailable for the failures
    that will fail every request after this one too, so the caller stops
    asking, and ValueError for a request that failed on its own.
    """
    if not token:
        raise Unavailable("no token")
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    # DeepSeek thinks by default, which bills reasoning tokens and ignores
    # temperature -- neither wanted for a translation. Only sent there: other
    # OpenAI-compatible endpoints may refuse a field they do not know.
    if "deepseek.com" in endpoint:
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "koreader-plugin-index",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # 401/403: the key is wrong or revoked. 402: the prepaid balance is
        # spent. 429: too many requests. None clears within a run.
        if exc.code in (401, 402, 403, 429):
            raise Unavailable(f"HTTP {exc.code}") from exc
        raise ValueError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"network error ({exc})") from exc

    try:
        answer = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        # 200 with no JSON is a retired or misrouted endpoint -- GitHub
        # Models answered a plain "OK" after it was shut down. Every request
        # after this one will get the same, so it is treated as unavailable.
        raise Unavailable(f"not a completion: {raw[:60]!r}") from exc
    answer = (answer or "").strip()
    if not acceptable(text, answer):
        raise Refused("answer is not an English rendering of the source")
    return answer
