"""Import-level guards for the Sniffer / OINK CORP boundary.

`app.research` (OINK CORP research) is the only code allowed to read data
after a frame's `frame_time` (forward-outcome labels). It may import
Sniffer's feature code — replay reuses it — but nothing in the live
observation pipeline may import research code, so future-looking labels
can never reach live Sniffs, the API, or the background services.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Entry points that legitimately drive research: the research package
# itself and its offline CLI.
_RESEARCH_SIDE = {
    APP_DIR / "research",
    APP_DIR / "scripts" / "generate_research_dataset.py",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_research_side(path: Path) -> bool:
    return any(path == side or side in path.parents for side in _RESEARCH_SIDE)


def test_live_pipeline_never_imports_research_code():
    offenders = {
        str(path.relative_to(APP_DIR)): sorted(
            m
            for m in _imported_modules(path)
            if m == "app.research" or m.startswith("app.research.")
        )
        for path in APP_DIR.rglob("*.py")
        if not _is_research_side(path)
    }
    assert {path: mods for path, mods in offenders.items() if mods} == {}


def test_guard_actually_sees_the_research_package():
    """Sanity check that the scan above isn't vacuous: research code exists
    and itself imports Sniffer's feature engine (replay reuses it)."""
    replay = APP_DIR / "research" / "replay.py"
    assert "app.features.engine" in _imported_modules(replay)
