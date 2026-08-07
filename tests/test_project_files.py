"""Contract tests for the project scaffolding (plan Todo 1)."""

from pathlib import Path

import pytest

REQUIRED_GITIGNORE_ENTRIES = (
    "downloads/",
    ".venv/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".omo/evidence/",
    "*.egg-info/",
    "dist/",
    "build/",
)

BROAD_PATTERNS = ("*", "**", "*.py")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _gitignore_lines() -> set[str]:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    return {
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.startswith("#")
    }


@pytest.mark.parametrize("entry", REQUIRED_GITIGNORE_ENTRIES)
def test_gitignore_contains_required_entry(entry: str) -> None:
    lines = _gitignore_lines()
    assert entry in lines, f".gitignore missing required entry: {entry!r}"


def test_gitignore_has_no_broad_patterns() -> None:
    lines = _gitignore_lines()
    for pattern in BROAD_PATTERNS:
        assert pattern not in lines, (
            f".gitignore contains unsupported broad pattern: {pattern!r}"
        )
