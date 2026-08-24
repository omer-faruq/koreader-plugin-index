"""What build_plugin makes of a README, by the shape of the README.

Four shapes reach the pipeline and they are meant to be handled differently:
plain English, Chinese with a translation beside it, Chinese with an English
section inside it, and Chinese alone. The rules that tell them apart accumulated
one measurement at a time and each has a fallback behind it, which is exactly
the kind of code that quietly stops doing what it says.

Nothing else covers build_plugin at all. The query suite reads a built index and
cannot see how a field was arrived at; this runs the function directly against
synthetic repositories, offline.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402
import extract  # noqa: E402

CHINESE = (
    "# 知乎日报\n\n"
    "在 KOReader 上阅读知乎日报，抓取内容并在本地构建 EPUB 打开，支持离线重读已缓存内容。\n"
)
TRANSLATION = (
    "# Zhihu Daily\n\n"
    "Read the Zhihu Daily digest inside KOReader. Articles are fetched and built "
    "into a local EPUB, and cached issues stay readable offline.\n"
)
BILINGUAL = CHINESE + (
    "\n## English\n\n"
    "Read the Zhihu Daily digest inside KOReader. Articles are fetched and built "
    "into a local EPUB, and cached issues stay readable offline.\n"
    "Every issue is kept on the device so it can be read again without a network.\n"
)
PLAIN_ENGLISH = (
    "# Tool\n\n"
    "An ordinary English readme that says plainly what the plugin does for you.\n"
)

CURATION = {"plugins": {}, "patches": {}, "distinctions": [],
            "glossary": {"知乎": "zhihu", "日报": "daily digest"}}


def repo(readme, sidecar=None, sidecar_name=None, description=""):
    node = {
        "nameWithOwner": "owner/thing.koplugin", "name": "thing.koplugin",
        "owner": {"login": "owner"}, "description": description, "url": "https://x",
        "stargazerCount": 1, "forkCount": 0, "isFork": False, "isArchived": False,
        "pushedAt": "2026-08-01T00:00:00Z", "createdAt": "2026-01-01T00:00:00Z",
        "licenseInfo": None, "defaultBranchRef": {"name": "main"},
        "repositoryTopics": {"nodes": []}, "root": {"entries": []},
        "readme": {"text": readme, "byteSize": len(readme.encode())},
    }
    if sidecar:
        node["readmeEnglish"] = sidecar
        node["readmeEnglishName"] = sidecar_name
    return node


def built(*args, **kwargs):
    return build.build_plugin(repo(*args, **kwargs), CURATION)


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)


def english_readme_is_left_alone():
    """The identity that keeps this whole feature off the other 750 plugins."""
    entry, detail, _ = built(PLAIN_ENGLISH)
    expect(entry["purpose"].startswith("An ordinary English"), entry["purpose"])
    expect("readme_source" not in detail, "an English repo must not be relabelled")
    expect(detail["readme_excerpt"].strip().startswith("# Tool"), detail["readme_excerpt"])


def a_translation_is_preferred_and_named():
    entry, detail, _ = built(CHINESE, TRANSLATION, "README_en.md")
    expect(extract.cjk_ratio(entry["purpose"]) < 0.15, entry["purpose"])
    expect(detail.get("readme_source") == "README_en.md", detail.get("readme_source"))
    expect(extract.cjk_ratio(detail["readme_excerpt"]) < 0.15,
           "the panel should show the translation the repository publishes")


def an_english_section_is_read_but_not_shown():
    """Sections are fragments. Good enough to extract from, not to display as
    the README -- so the excerpt stays the document the repository wrote."""
    entry, detail, _ = built(BILINGUAL)
    expect(extract.cjk_ratio(entry["purpose"]) < 0.15, entry["purpose"])
    expect("readme_source" not in detail, "fragments must not be named as a file")
    expect(extract.cjk_ratio(detail["readme_excerpt"]) >= 0.15,
           "the panel should still show the original for a bilingual README")


def chinese_alone_keeps_its_own_words():
    entry, detail, _ = built(CHINESE)
    expect(extract.cjk_ratio(entry["purpose"]) >= 0.15,
           "no rule can turn a monolingual document into another language")
    expect("readme_source" not in detail, detail)
    expect({"zhihu", "daily", "digest"} <= set(entry["keywords"]),
           f"glossary labels missing: {entry['keywords']}")


def an_english_description_beats_a_chinese_purpose():
    entry, _, _ = built(CHINESE, description="Read the Zhihu Daily digest offline.")
    expect(entry["purpose"] == "Read the Zhihu Daily digest offline.", entry["purpose"])


def a_chinese_description_does_not_displace_anything():
    entry, _, _ = built(CHINESE, description="在 KOReader 上阅读知乎日报。")
    expect(extract.cjk_ratio(entry["purpose"]) >= 0.15,
           "swapping one Chinese sentence for another gains nothing")


def nothing_extracted_is_never_worse_than_before():
    """The fallback that cost seven plugins their purpose before it was split
    in two: a filtered view yielding no prose must not empty the entry."""
    thin = CHINESE + "\n## English\n\n`cp -r thing.koplugin plugins/`\n"
    entry, _, _ = built(thin)
    expect(entry["purpose"], "an entry must not lose the purpose it had")


CASES = [
    ("an English README is left alone", english_readme_is_left_alone),
    ("a translation is preferred and named", a_translation_is_preferred_and_named),
    ("an English section is read but not shown", an_english_section_is_read_but_not_shown),
    ("Chinese alone keeps its own words, plus labels", chinese_alone_keeps_its_own_words),
    ("an English description beats a Chinese purpose", an_english_description_beats_a_chinese_purpose),
    ("a Chinese description displaces nothing", a_chinese_description_does_not_displace_anything),
    ("extracting nothing is never worse than before", nothing_extracted_is_never_worse_than_before),
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
        print(f"\n{len(failures)}/{len(CASES)} README shape checks failed", file=sys.stderr)
        return 1
    print(f"\nREADME shapes ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
