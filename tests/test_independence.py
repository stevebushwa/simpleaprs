"""The packages in this repo keep to one dependency direction: apps depend on piwebkit, piwebkit depends on no app,
and simpleaprs and simplemesh don't know about each other. That is what lets either one be split out, or used alone.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ("simpleaprs", "simplemesh")
ALLOWED = {"piwebkit": set(), "simpleaprs": {"piwebkit"}, "simplemesh": {"piwebkit"}}


def imported_repo_packages(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else \
                [a.name for a in node.names] if isinstance(node, ast.Import) else []
        found |= {n.split(".")[0] for n in names} & set(ALLOWED)
    return found


def test_packages_only_import_what_they_may():
    for pkg, allowed in ALLOWED.items():
        for f in (ROOT / pkg).rglob("*.py"):
            bad = imported_repo_packages(f) - allowed - {pkg}
            assert not bad, f"{f.relative_to(ROOT)} imports {sorted(bad)}"


def test_the_apps_do_not_read_each_others_files():
    """Not through imports, either: no app looks in another's config or cache directory."""
    for pkg, other in (("simpleaprs", "simplemesh"), ("simplemesh", "simpleaprs")):
        for f in (ROOT / pkg).rglob("*.py"):
            for n, line in enumerate(f.read_text().splitlines(), 1):
                code = line.split("#")[0]
                assert f'"{other}"' not in code and f"'{other}'" not in code, f"{f.relative_to(ROOT)}:{n} names {other}"


def test_the_shared_web_files_live_in_piwebkit():
    for name in ("login.html", "maplibre-gl.js", "maplibre-gl.css", "pmtiles.js"):
        assert (ROOT / "piwebkit" / "web" / name).is_file()
        for pkg in APPS:
            assert not (ROOT / pkg / "web" / name).exists(), f"{pkg} has its own copy of {name}"
