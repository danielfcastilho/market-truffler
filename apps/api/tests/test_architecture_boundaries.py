"""Import-level guards for the Sniffer / OINK CORP boundary.

`app.research` (OINK CORP research) is the only code allowed to read data
after a frame's `frame_time` (forward-outcome labels). It may import
Sniffer's feature code — replay reuses it — but nothing in the live
observation pipeline may import research code, so future-looking labels
can never reach live Sniffs, the API, or the background services.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# Entry points that legitimately drive research: the research package
# itself and its offline CLIs.
_RESEARCH_SIDE = {
    APP_DIR / "research",
    APP_DIR / "scripts" / "generate_research_dataset.py",
    APP_DIR / "scripts" / "run_experiment.py",
}

# Experiments run on materialized Parquet only — never the database, never
# Sniffer's live services or feature computation.
_PARQUET_ONLY = [
    APP_DIR / "research" / "experiment.py",
    APP_DIR / "research" / "evaluation.py",
    APP_DIR / "research" / "runs.py",
    APP_DIR / "scripts" / "run_experiment.py",
]
_FORBIDDEN_FOR_EXPERIMENTS = (
    "sqlalchemy",
    "app.db",
    "app.repositories",
    "app.services",
    "app.features",
    "app.research.replay",
    "app.research.forward_returns",
)


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


@pytest.mark.parametrize("path", _PARQUET_ONLY, ids=lambda p: p.name)
def test_experiment_modules_never_touch_the_database_or_live_pipeline(path):
    imported = _imported_modules(path)
    assert (
        sorted(
            m
            for m in imported
            if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN_FOR_EXPERIMENTS)
        )
        == []
    )
