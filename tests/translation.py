"""Machine translation of READMEs that offer no English at all.

The one step in the build that asks a model, so the rules around it are what
keep it honest: it is the last resort after everything the repository says in
English itself, it retires the day the author publishes a README_en, it is
labelled wherever it shows, and a failing or refusing service costs that night's
translations and never the build.

None of that is visible from a finished index, and the real service is not
reachable from a test. The translator here is scripted, and build_plugin and
attach_translations run directly against synthetic repositories, offline.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402
import extract  # noqa: E402
import translate  # noqa: E402

CHINESE = (
    "# 知乎日报\n\n"
    "在 KOReader 上阅读知乎日报，抓取内容并在本地构建 EPUB 打开，支持离线重读已缓存内容。\n\n"
    "## 功能\n\n"
    "- 离线阅读已缓存的日报\n"
    "- 自动生成 EPUB\n"
)
ENGLISH = (
    "# Zhihu Daily\n\n"
    "Read the Zhihu Daily digest inside KOReader. Articles are fetched and built "
    "into a local EPUB, and cached issues stay readable offline.\n\n"
    "## Features\n\n"
    "- Read cached issues offline\n"
    "- Builds the EPUB automatically\n"
)
BILINGUAL = CHINESE + (
    "\n## English\n\n"
    "Read the Zhihu Daily digest inside KOReader. Articles are fetched and built "
    "into a local EPUB, and cached issues stay readable offline.\n"
    "Every issue is kept on the device so it can be read again without a network.\n"
)
RUSSIAN = (
    "# Загрузчик\n\n"
    "Плагин для KOReader, который скачивает книги из Telegram прямо на устройство "
    "и открывает их в библиотеке без компьютера.\n"
)
PLAIN_ENGLISH = (
    "# Tool\n\n"
    "An ordinary English readme that says plainly what the plugin does for you.\n"
)

CURATION = {"plugins": {}, "patches": {}, "distinctions": [], "glossary": {}}


def repo(readme, name="owner/thing.koplugin", stars=20, sidecar=None, description=""):
    owner, _, short = name.partition("/")
    node = {
        "nameWithOwner": name, "name": short,
        "owner": {"login": owner}, "description": description, "url": "https://x",
        "stargazerCount": stars, "forkCount": 0, "isFork": False, "isArchived": False,
        "pushedAt": "2026-08-01T00:00:00Z", "createdAt": "2026-01-01T00:00:00Z",
        "licenseInfo": None, "defaultBranchRef": {"name": "main"},
        "repositoryTopics": {"nodes": []}, "root": {"entries": []},
        "readme": {"text": readme, "byteSize": len(readme.encode())},
    }
    if sidecar:
        node["readmeEnglish"] = sidecar
        node["readmeEnglishName"] = "README_en.md"
    return node


class Scripted:
    """A translator that answers from a script and remembers what it was asked."""

    def __init__(self, answer=ENGLISH, fail=None):
        self.answer, self.fail, self.asked = answer, fail, []

    def __call__(self, text):
        self.asked.append(text)
        if self.fail:
            raise self.fail
        return self.answer


def run(nodes, cache=None, translator=None, **kwargs):
    nodes = {n["nameWithOwner"]: n for n in nodes}
    cache = {} if cache is None else cache
    released = build.attach_translations(nodes, cache, translator, **kwargs)
    return nodes, cache, released


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)


def a_chinese_readme_is_translated_and_labelled():
    nodes, cache, _ = run([repo(CHINESE)], translator=Scripted())
    entry, detail, _ = build.build_plugin(nodes["owner/thing.koplugin"], CURATION)
    expect(entry.get("machine_translated") is True, entry)
    expect(entry["purpose"].startswith("Read the Zhihu Daily"), entry["purpose"])
    expect("offline" in " ".join(entry["features"]).lower(), entry["features"])
    expect(detail.get("readme_translated") is True, detail)
    expect(not extract.foreign_script(detail["readme_excerpt"]), detail["readme_excerpt"])
    expect("owner/thing.koplugin" in cache, "a translation must be kept for the next run")


def the_authors_english_description_stays_the_purpose():
    node = repo(CHINESE, description="Zhihu Daily for KOReader.")
    nodes, _, _ = run([node], translator=Scripted())
    entry, detail, _ = build.build_plugin(nodes["owner/thing.koplugin"], CURATION)
    expect(entry["purpose"] == "Zhihu Daily for KOReader.", entry["purpose"])
    expect(entry.get("machine_translated") and detail.get("readme_translated"),
           "the README panel still shows the translation, and says so")


def an_authors_translation_is_never_replaced():
    translator = Scripted()
    nodes, _, _ = run([repo(CHINESE, sidecar=ENGLISH), repo(BILINGUAL, name="o/b")],
                      translator=translator)
    expect(not translator.asked, "a README_en or an English section needs no model")
    entry, detail, _ = build.build_plugin(nodes["owner/thing.koplugin"], CURATION)
    expect("machine_translated" not in entry and "readme_translated" not in detail, detail)


def a_new_readme_en_retires_the_translation():
    """What was asked for in so many words: once the author offers English,
    that is what gets used, and the translation is let go."""
    _, cache, _ = run([repo(CHINESE)], translator=Scripted())
    translator = Scripted()
    nodes, cache, released = run([repo(CHINESE, sidecar=ENGLISH)], cache, translator)
    expect(released == {"owner/thing.koplugin"}, released)
    expect(not translator.asked, "nothing to translate once README_en exists")
    entry, detail, _ = build.build_plugin(nodes["owner/thing.koplugin"], CURATION)
    expect("machine_translated" not in entry, entry)
    expect(detail.get("readme_source") == "README_en.md", detail)


def english_is_left_alone_and_stars_do_not_matter():
    """No threshold: the cap and the order are the only limit, and an
    unstarred plugin is the one English search can least reach otherwise."""
    translator = Scripted()
    run([repo(PLAIN_ENGLISH), repo(CHINESE, name="o/unstarred", stars=0)],
        translator=translator)
    expect(len(translator.asked) == 1, f"asked for {len(translator.asked)}")
    expect(not extract.foreign_script(PLAIN_ENGLISH), "the English one is never sent")


def other_scripts_count_too():
    translator = Scripted()
    run([repo(RUSSIAN)], translator=translator)
    expect(len(translator.asked) == 1, "a Russian README is as unreadable as a Chinese one")


def an_unchanged_readme_is_not_paid_for_twice():
    _, cache, _ = run([repo(CHINESE)], translator=Scripted())
    again = Scripted()
    nodes, _, _ = run([repo(CHINESE)], cache, again)
    expect(not again.asked, "the monthly rebuild must reuse what it has")
    expect(nodes["owner/thing.koplugin"].get("readmeTranslated"), "reused, not dropped")

    edited = Scripted()
    run([repo(CHINESE + "\n新增：夜间模式。\n")], cache, edited)
    expect(len(edited.asked) == 1, "an edited README is translated again")


def a_stored_translation_survives_a_night_without_the_service():
    _, cache, _ = run([repo(CHINESE)], translator=Scripted())
    nodes, _, _ = run([repo(CHINESE)], cache, translator=None)
    expect(nodes["owner/thing.koplugin"].get("readmeTranslated"),
           "--no-translate, or no service, still uses what is already paid for")


def an_unavailable_service_stops_asking_but_not_the_build():
    translator = Scripted(fail=translate.Unavailable("HTTP 403"))
    nodes, cache, _ = run([repo(CHINESE, name=f"o/r{i}") for i in range(5)],
                          translator=translator)
    expect(len(translator.asked) == 1, f"kept asking: {len(translator.asked)}")
    expect(not cache and not any(n.get("readmeTranslated") for n in nodes.values()), cache)
    entry, _, _ = build.build_plugin(nodes["o/r0"], CURATION)
    expect("machine_translated" not in entry, "untranslated is the old behaviour, not an error")


def one_bad_answer_costs_one_repository():
    class OnceBad(Scripted):
        def __call__(self, text):
            self.asked.append(text)
            if len(self.asked) == 1:
                raise ValueError("summarised")
            return self.answer
    translator = OnceBad()
    _, cache, _ = run([repo(CHINESE, name=f"o/r{s}", stars=s) for s in (30, 20, 10)],
                      translator=translator)
    expect(len(translator.asked) == 3, "a bad answer is not a reason to stop")
    expect(set(cache) == {"o/r20", "o/r10"}, sorted(cache))


def no_error_of_any_kind_reaches_the_build():
    """Whatever the service does, the build goes on: an exception nobody
    anticipated is caught like the rest, and a service failing every time
    is given up on after three rather than timed out thirty times."""
    translator = Scripted(fail=RuntimeError("connection reset"))
    nodes, cache, _ = run([repo(CHINESE, name=f"o/r{i}") for i in range(6)],
                          translator=translator)
    expect(len(translator.asked) == 3, f"asked {len(translator.asked)} times")
    expect(not cache, cache)
    entry, _, _ = build.build_plugin(nodes["o/r0"], CURATION)
    expect(entry["purpose"], "an untranslated entry is built exactly as before")


def a_refusal_is_not_asked_again():
    """Same text at temperature zero, same answer: asking nightly would only
    hold a slot. An edited README is a new question and is asked."""
    first = Scripted(fail=translate.Refused("echo"))
    _, cache, _ = run([repo(CHINESE)], translator=first)
    expect(cache["owner/thing.koplugin"].get("refused"), cache)
    again = Scripted()
    nodes, _, _ = run([repo(CHINESE)], cache, again)
    expect(not again.asked, "a refusal must be remembered")
    expect(not nodes["owner/thing.koplugin"].get("readmeTranslated"), "nothing to show")
    edited = Scripted()
    nodes, _, _ = run([repo(CHINESE + "\n新增：夜间模式。\n")], cache, edited)
    expect(len(edited.asked) == 1 and nodes["owner/thing.koplugin"].get("readmeTranslated"),
           "an edited README is asked again")


def refusals_do_not_stop_the_run():
    translator = Scripted(fail=translate.Refused("echo"))
    run([repo(CHINESE, name=f"o/r{i}") for i in range(5)], translator=translator)
    expect(len(translator.asked) == 5, "a service that answers is not a service that is down")


def the_backlog_is_worked_through_between_full_builds():
    """A diff run rebuilds only what was pushed, so what the cap left over is
    fetched by name, most-starred first -- and nothing that needs no model."""
    excerpt = lambda text: {"readme_excerpt": extract.clean_markdown(text)}  # noqa: E731
    previous = [
        {"id": "o/waiting-big", "stars": 40},
        {"id": "o/waiting-small", "stars": 0},
        {"id": "o/waiting-mid", "stars": 9},
        {"id": "o/done", "stars": 99, "machine_translated": True},
        {"id": "o/has-readme-en", "stars": 98},
        {"id": "o/bilingual", "stars": 97},
        {"id": "o/english", "stars": 96},
        {"id": "o/refused", "stars": 95},
        {"id": "o/pushed-today", "stars": 94},
    ]
    details = {
        "o/waiting-big": excerpt(CHINESE), "o/waiting-small": excerpt(RUSSIAN),
        "o/waiting-mid": excerpt(CHINESE), "o/done": excerpt(ENGLISH),
        "o/has-readme-en": {**excerpt(ENGLISH), "readme_source": "README_en.md"},
        "o/bilingual": excerpt(BILINGUAL), "o/english": excerpt(PLAIN_ENGLISH),
        "o/refused": excerpt(CHINESE), "o/pushed-today": excerpt(CHINESE),
    }
    cache = {"o/refused": {"source_sha": "x", "refused": True}}
    have = {"o/pushed-today": {}}
    got = build.translation_backlog(previous, details, have, cache, limit=2)
    expect(got == ["o/waiting-big", "o/waiting-mid"], got)
    got = build.translation_backlog(previous, details, have, cache)
    expect(got == ["o/waiting-big", "o/waiting-mid", "o/waiting-small"], got)


def the_cap_goes_to_the_most_starred():
    translator = Scripted()
    nodes, cache, _ = run([repo(CHINESE, name=f"o/r{s}", stars=s) for s in (6, 90, 30)],
                          translator=translator, limit=2)
    expect(set(cache) == {"o/r90", "o/r30"}, sorted(cache))


def echoes_and_summaries_are_refused():
    source, _ = translate.source_text(CHINESE)
    expect(not translate.acceptable(source, source), "an echo is not a translation")
    expect(not translate.acceptable(source, "A plugin."), "a summary is not a translation")
    expect(translate.acceptable(source, ENGLISH), "a faithful rendering is accepted")


def a_long_readme_is_cut_and_says_so():
    long = CHINESE + "\n".join(f"- 第{i}项功能说明，内容较长以便超过限制。" for i in range(400))
    text, partial = translate.source_text(long)
    expect(partial and len(text) <= translate.MAX_SOURCE_CHARS, (partial, len(text)))
    nodes, _, _ = run([repo(long)], translator=Scripted())
    _, detail, _ = build.build_plugin(nodes["owner/thing.koplugin"], CURATION)
    expect(detail.get("readme_partial") is True, "the panel must say it shows the opening")


CASES = [
    ("a Chinese README is translated and labelled", a_chinese_readme_is_translated_and_labelled),
    ("the author's English description stays the purpose", the_authors_english_description_stays_the_purpose),
    ("an author's translation is never replaced", an_authors_translation_is_never_replaced),
    ("a new README_en retires the translation", a_new_readme_en_retires_the_translation),
    ("English is left alone, and stars do not matter", english_is_left_alone_and_stars_do_not_matter),
    ("other scripts count too", other_scripts_count_too),
    ("an unchanged README is not paid for twice", an_unchanged_readme_is_not_paid_for_twice),
    ("a stored translation survives a night without the service", a_stored_translation_survives_a_night_without_the_service),
    ("an unavailable service stops asking, not the build", an_unavailable_service_stops_asking_but_not_the_build),
    ("one bad answer costs one repository", one_bad_answer_costs_one_repository),
    ("no error of any kind reaches the build", no_error_of_any_kind_reaches_the_build),
    ("a refusal is not asked again", a_refusal_is_not_asked_again),
    ("refusals do not stop the run", refusals_do_not_stop_the_run),
    ("the backlog is worked through between full builds", the_backlog_is_worked_through_between_full_builds),
    ("the cap goes to the most starred", the_cap_goes_to_the_most_starred),
    ("echoes and summaries are refused", echoes_and_summaries_are_refused),
    ("a long README is cut, and says so", a_long_readme_is_cut_and_says_so),
]


def main():
    failures = []
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            print(f"  FAIL  {name}\n          {exc}", file=sys.stderr)
            failures.append(name)
        else:
            print(f"  ok    {name}")
    if failures:
        print(f"\n{len(failures)}/{len(CASES)} translation checks failed", file=sys.stderr)
        return 1
    print(f"\nTranslation ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
