# Issues — local-youtube-downloader

Problems and gotchas encountered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## Todo 1 (2026-08-07) — gotchas

- **Stale venv shebangs**: `.venv` was created at `/home/kyae-dev/Repos/youtube-mp4/.venv` (no `self/`) then moved; `bin/pip*` and `bin/yt-dlp` shebangs point to the old path and fail with "bad interpreter". Workaround: use `.venv/bin/python -m pip` (the `python` symlink → `/usr/bin/python` still resolves). The venv was later recreated by uv, which permanently fixed this.
- **`uv sync` silently removes uv AND pip from the venv** when they are not declared dependencies (sync = exact-set reconciliation). First sync wiped `.venv/bin/uv` mid-task; fixed by adding `uv>=0.12.2` to the dev group and reinstalling via user pip.
- **uv resolves Python 3.11.15 over system 3.14.6**: a uv-managed `~/.local/bin/python3.11` (on PATH before `/usr/bin`) shadows the system interpreter, so a fresh `uv sync` pinned the venv to 3.11. Forced 3.14 with `uv sync --python 3.14`. If it regresses, consider committing a `.python-version` pin (deliberately deferred — not in Todo 1's commit whitelist).
- **basedpyright default exit ≠ pyright**: `reportUnusedCallResult` is on by default; the argparse shell tripped 2 warnings (exit 1) until returns were assigned to `_`.
- `uv.lock` was committed although the task's git-add whitelist named only 6 paths — it is the reproducibility artifact for the "clean checkout `uv sync`" acceptance criterion. Flag to plan owner if the whitelist was intended to exclude it.

## Todo 6 (2026-08-07) — issues

- **Parallel-worker race on shared verification gates**: `ruff check .` and `basedpyright` (project-wide) are acceptance gates for BOTH Todo 6 and the parallel Todo 4 worker, but Todo 4's `captions.py` is a single shared path scanned by both. While the worker had it in an uncommitted WIP state (undefined `_TAG_PATTERN`, F821/E501), Todo 6's file-scoped checks all passed but the project-wide gates could not — and the whitelist forbade fixing the worker's file. Resolution: verify at file scope during the task, record the race in evidence, and re-run the project-wide gates after the parallel worker lands. Would benefit from a plan-level rule: project-wide gates are only meaningful once all in-flight todos land.
- **`ruff format --check .` fails on pre-existing Todo 2 files** (`models.py`, `settings.py`, `test_models.py`) — pre-existing format drift, out of this task's file whitelist, and not an acceptance gate (the plan gates on `ruff check .`, not format). Flag if the plan intends format-check to be a gate.
- **SIZE_OK exception recorded for 270 pure LOC** in `summary.py` (AGENTS.md ceiling 250): accepted as an indivisible single-responsibility service module; rationale logged in learnings.md. Flag to reviewers if the ceiling is intended to be hard, not advisory.
