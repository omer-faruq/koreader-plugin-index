"""Which repository layouts count as holding a KOReader plugin.

This is the field the whole catalogue hangs on: without the marker an entry is
tier C, and tier C is behind a checkbox on the page and excluded outright from
the AI shortlist. It got that consequential quietly, and for most of the
catalogue's life it asked the wrong question -- `_meta.lua` at the repository
root -- which hid 34 of the 75 entries it was applied to, some with hundreds of
stars.

Two facts fix it, and both are easy to lose again in a refactor, so they are
pinned here. KOReader runs `main.lua` and treats `_meta.lua` as optional
metadata (`frontend/pluginloader.lua`). And a repository may either be the
plugin folder or contain it one level down, under a directory name nobody can
enumerate in advance.

Offline: the marker is pure, and the layouts below are the ones actually
observed in the catalogue.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import build  # noqa: E402


def tree(*names):
    """A tree from `dir/file` paths. One level deep, like the query."""
    def kind(name):
        # KOReader discovers *.koplugin directories, so in these fixtures a name
        # ending that way is a directory -- which is the thing being tested.
        return "tree" if name.endswith(".koplugin") else "blob"

    root = {}
    for name in names:
        head, sep, tail = name.partition("/")
        if not sep:
            root[head] = None
            continue
        root.setdefault(head, [])
        if tail:
            root[head].append(tail)

    entries = []
    for name, children in root.items():
        if children is None:
            entries.append({"name": name, "type": kind(name)})
        else:
            entries.append({"name": name, "type": "tree", "object": {
                "entries": [{"name": c, "type": kind(c)} for c in children]}})
    return {"root": {"entries": entries}}


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)


def a_plugin_at_the_root_is_found():
    """The commonest shape: the repository *is* the plugin folder."""
    expect(build.has_plugin_marker(tree("main.lua", "README.md")),
           "main.lua at the root is a plugin")


def a_plugin_without_meta_is_still_a_plugin():
    """The bug this file exists for. KOReader loads main.lua and reads
    _meta.lua only if it happens to parse, so a plugin without one installs and
    runs -- confirmed on a device. Requiring it demoted real plugins."""
    expect(build.has_plugin_marker(tree("main.lua")),
           "a plugin without _meta.lua is a plugin")


def meta_alone_still_counts():
    """Purely additive: everything the old root rule accepted, this accepts."""
    expect(build.has_plugin_marker(tree("_meta.lua")), "_meta.lua alone counts")
    expect(build.has_plugin_marker(tree("thing.koplugin/main.lua")),
           "a *.koplugin directory at the root counts")


def a_plugin_one_level_down_is_found():
    """`src/`, `plugin/`, and names nobody has used yet. Half the misses."""
    for shape in ("src/main.lua", "plugin/_meta.lua", "lib/main.lua"):
        expect(build.has_plugin_marker(tree("README.md", shape)),
               f"{shape} is a plugin one level down")


def a_koplugin_directory_one_level_down_is_found():
    """`apps/readest.koplugin`, `plugins/koinsight.koplugin`, `dist/x.koplugin`."""
    expect(build.has_plugin_marker(tree("apps/readest.koplugin")),
           "a *.koplugin directory one level down is a plugin")


def a_repository_with_no_plugin_is_not_one():
    """The 32 that are genuinely something else: converters, dashboards,
    icon packs, scripts. Demoting these is the field doing its job."""
    expect(not build.has_plugin_marker(tree("README.md", "src/convert.js")),
           "a repository with no loadable file is not a plugin")
    expect(not build.has_plugin_marker(tree("README.md")),
           "a README alone is not a plugin")
    expect(not build.has_plugin_marker({"root": {"entries": []}}),
           "an empty repository is not a plugin")


def the_search_stops_at_one_level():
    """A main.lua further down belongs to something else in the repository more
    often than it is the plugin. The observed shapes all sit at depth one."""
    node = tree("tools/vendor")
    node["root"]["entries"][0]["object"]["entries"] = [
        {"name": "vendor", "type": "tree",
         "object": {"entries": [{"name": "main.lua", "type": "blob"}]}}]
    expect(not build.has_plugin_marker(node),
           "main.lua two levels down is not the plugin")


def an_unexpanded_tree_falls_back_to_the_root():
    """A cached response from before the query was nested, or a directory
    GitHub declined to walk. Absent children mean "nothing found here", never
    an error -- the root rule still stands on its own."""
    node = {"root": {"entries": [
        {"name": "src", "type": "tree"},
        {"name": "main.lua", "type": "blob"}]}}
    expect(build.has_plugin_marker(node),
           "an unexpanded directory must not stop the root rule")
    expect(not build.has_plugin_marker(
        {"root": {"entries": [{"name": "src", "type": "tree"}]}}),
        "an unexpanded directory reports nothing found, not a crash")
    expect(not build.has_plugin_marker({}), "a node with no tree is not a plugin")


def a_file_named_like_a_directory_is_not_one():
    """`.koplugin` counts as a marker because KOReader discovers directories by
    that suffix. A blob with the same name is not what it discovers."""
    expect(not build.has_plugin_marker(
        {"root": {"entries": [{"name": "notes.koplugin", "type": "blob"}]}}),
        "a *.koplugin blob is not a plugin directory")


CASES = [
    ("a plugin at the root is found", a_plugin_at_the_root_is_found),
    ("a plugin without _meta.lua is still a plugin", a_plugin_without_meta_is_still_a_plugin),
    ("_meta.lua and *.koplugin still count", meta_alone_still_counts),
    ("a plugin one level down is found", a_plugin_one_level_down_is_found),
    ("a *.koplugin directory one level down is found", a_koplugin_directory_one_level_down_is_found),
    ("a repository with no plugin is not one", a_repository_with_no_plugin_is_not_one),
    ("the search stops at one level", the_search_stops_at_one_level),
    ("an unexpanded tree falls back to the root", an_unexpanded_tree_falls_back_to_the_root),
    ("a file named like a directory is not one", a_file_named_like_a_directory_is_not_one),
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
        print(f"\n{len(failures)}/{len(CASES)} plugin marker checks failed", file=sys.stderr)
        return 1
    print(f"\nPlugin marker ok across {len(CASES)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
