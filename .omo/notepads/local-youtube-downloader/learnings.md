# Learnings — local-youtube-downloader

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /start-work. Append new entries below - never overwrite._

---

## Todo 1 (2026-08-07) — git init + uv bootstrap facts

- Repo was NOT a git repo; `git init` (root commit) required before anything else.
- `uv` bootstrap: install into the project venv via `pip install uv` (never the astral curl installer). `uv` 0.12.2 installs as a native ELF binary in `.venv/bin/`, so it is immune to the stale-shebang issue that breaks the venv's pip wrapper scripts.
- **uv must be a dev dependency** (`uv>=0.12.2` in `[dependency-groups] dev`): `uv sync` synchronizes the venv to exactly the declared dependency set and will otherwise uninstall both `uv` and `pip` from the venv. With `uv` in the dev group, `.venv/bin/uv` survives every sync.
- Restoring a broken venv: `~/.local/bin/pip --python .venv/bin/python install uv` (user-level pip 26.1.2) reinstalls uv into a venv that lost its pip — `--python` flag must precede the `pip` subcommand.
- `uv sync --python 3.14` recreates the venv on a chosen interpreter without a `.python-version` pin file; subsequent syncs reuse the now-valid venv.
- Deterministic identical `--help` from `python main.py` and `python -m youtube_downloader`: set `prog="youtube-downloader"` explicitly in `ArgumentParser`, otherwise argparse defaults `prog` to the invoked module name (`main.py` vs `__main__.py`).
- basedpyright flags unused call results (`reportUnusedCallResult`): assign `add_argument(...)`/`parse_args()` returns to `_`; this keeps `basedpyright` at exit 0.

## Todo 2 (2026-08-07) — domain contracts

- `Path("downloads")` as a frozen-dataclass default trips ruff B008 (function call in argument default); use `field(default_factory=lambda: Path("downloads"))` instead.
- basedpyright `reportUnannotatedClassAttribute` fires for unannotated class attrs on non-`@final` classes — annotate every `default_message: str` on the error subclasses.
- `reportImplicitStringConcatenation` rejects mixing f-string and plain adjacent literals; join with explicit `+` instead.
- Ordering in `safe_title`: collapse whitespace runs FIRST, then replace unsafe/control chars — otherwise tab/newline (0x09/0x0a, inside `\x00-\x1f`) become `_` instead of spaces and never collapse.
- `urlsplit` lowercases scheme+hostname but does NOT raise for malformed brackets; bracket-stripping of `[::1]` is automatic via `.hostname`. Validate `hostname is None` explicitly (empty `http://` gives None, and `ip_address(None)` raises TypeError not ValueError).
- ruff's isort wants alphabetical order inside `from ... import (...)` blocks (DownloaderError before DownloadRequest); `ruff check --fix` resolves it.
- Loopback detection: `ipaddress.ip_address(host).is_loopback` accepts the whole 127.0.0.0/8 range and canonical ::1 forms; add "localhost"/"::1" name checks before it since `ip_address("localhost")` raises.

## Todo 6 (2026-08-07) — local Ollama summary service

- **basedpyright `reportImplicitStringConcatenation` fires only inside function-call arguments** spanning lines (likely-missing-comma hazard), not for assignment/`return`-parenthesized adjacency. `parts.append("a" "b")` trips it; `x = ("a" "b")` and `return ("a" "b")` do not. Fix with explicit `+` inside calls (settings.py already did this).
- **`reportAny` cannot be silenced by annotating the target** (`payload: object = json.loads(body)` still warns "Type of payload is Any"). `typing.cast` at the parse boundary is the working pattern: `cast(dict[str, object], json.loads(body))` — and cast's Any argument is NOT flagged. But narrowing `object`→`isinstance(x, list)`/`dict` leaks `reportUnknown*` warnings (Unknown type args); cast the collection too: `cast(list[dict[str, object]], models)`. `urllib.request.urlopen` returns Any → `cast(http.client.HTTPResponse, ...)` + manual `response.close()` in try/finally (no `with`).
- **Ollama `GET /api/tags` reports pulled models as `name:tag`** (e.g. `llama3.2:latest` after `ollama pull llama3.2`). Exact-name matching would false-negative every real install, so match configured base name against `name.split(":", 1)[0]` too — still rejects wrong families (`llama3.1` vs `llama3.2`).
- `urllib.error.URLError` and `socket.timeout` are both `OSError` subclasses — a single `except (OSError, http.client.HTTPException)` in the request seam covers unreachable + timeout + bad status lines.
- basedpyright flags unused results of `TextIO.write()` (returns int) → `_ = handle.write(content)`.
- Deterministic chunk packing: accumulate lines until adding the next would exceed `max_chars`; never split a line; join with `"\n"`. Hand-computable for tests (4×50-char lines @ max_chars=110 → exactly 2 chunks).
- 270 pure LOC for the whole service module: accepted as SIZE_OK — one noun phrase ("local goal-directed Ollama summarization"), all private helpers serve the single `summarize` protocol method; splitting would create single-caller modules under 80 lines (worse smell).

## Todo 4 (2026-08-07) — caption transcript acquisition

- **Installed yt-dlp is 2026.07.04, NOT 2026.2.4** as the plan context claimed. Dist-info lives at `.venv/lib64/python3.14/site-packages/yt_dlp-2026.7.4.dist-info/` — note `lib64`, not `lib` (lib64 is a symlink on this box).
- **yt-dlp surface needed** (verified against 2026.07.04 source): `YoutubeDL(..., skip_download=True)` + `ydl.extract_info(url, download=False)` inside a `with` block (context manager closes the request director); subtitle/caption metadata arrives as `info["subtitles"]` (manual) and `info["automatic_captions"]` (auto), each `dict[lang, list[entry]]` with `entry["url"]`, `entry["ext"]` (`vtt`/`srv3`/`json3`), and sometimes inline `entry["data"]`; `info["http_headers"]` carries required request headers for caption download. `process_subtitles()` (`subtitleslangs`, defaults to `en`-only) is NOT suitable — write custom resolution (manual-first, exclude `live_chat`/`danmaku`, any language) instead.
- **Live-chat pseudo-tracks**: yt-dlp emits `live_chat`/`live_chat_replay`/`danmaku*` keys inside the caption dicts; they must be filtered (they have no transcript semantics and would otherwise be "selected").
- **Don't import yt-dlp in library code**: inject the surface via a `ydl_factory: Callable[[Mapping[str, object]], CaptionYdl]` (context-managed Protocol). Tests substitute a fake; fixtures stay plain dicts; no network, ever. `cast()` at the metadata boundary (`cast(dict[str, list[dict[str, object]]], raw)`) matches the Todo 6 `reportAny`/`reportUnknown` pattern.
- **VTT cleanup order matters**: strip `<...>` tags BEFORE `html.unescape` — a literal `&lt;c&gt;` in the payload must survive as `<c>`, not get eaten as a tag. (Doing unescape first is the bug.)
- **WebVTT subset to handle**: optional `WEBVTT` header (also tolerate a leading BOM before it), `NOTE` blocks (skip until next blank line), optional cue identifiers (line without `-->`), timestamps `HH:MM:SS.mmm` and `MM:SS.mmm` (comma or dot decimal), cue settings after `-->` (take only the end-time token), blank-line block separators. Lenient skip of malformed cue lines; empty-cue drop; non-monotonic start times → `CaptionsUnavailableError`.
- **Error contract** (load-bearing for Todo 7's ASR fallback): `CaptionsUnavailableError` = no usable track / only live chat / empty-language key / empty-or-malformed VTT / non-time-ordered cues (fall back to speech-to-text); `TranscriptError` = metadata fetch failure, caption-download network failure (bounded retry), transcript write failure (remediation, never silent).
- **Atomic transcript write**: `tempfile.mkstemp(dir=path.parent, prefix=".transcript-", suffix=".tmp")` + `os.replace` with `os.unlink` cleanup on failure; `path.parent.mkdir(parents=True, exist_ok=True)` first. `os.fdopen` the raw descriptor for `encoding="utf-8"`.
- **351 pure LOC in `captions.py`**: accepted over the 250 soft ceiling — the orchestrator's commit pass whitelists exactly `captions.py` + `test_captions.py`, so a `vtt.py` split would break the sequential commit; the module is one noun phrase (caption acquisition: resolution → parse → render → write). Flagged for the final review wave, which may split it.

## Todo 5 (2026-08-07) — local faster-whisper transcription fallback

- **faster-whisper 1.2.1 installs cleanly on Python 3.14.6**: `uv add faster-whisper` resolved with no wheel blocker — ctranslate2 4.8.1 and onnxruntime 1.28.0 both ship cp314 wheels. Lands in `[project] dependencies` as `faster-whisper>=1.2.1` + `uv.lock` atomically. Verified API surface against the README: `WhisperModel(model, device=..., compute_type=...)`, `segments, info = model.transcribe(path, language=None)`, `segments` is a lazy generator (`segment.start/.end/.text`), `info.language`/`info.language_probability`; models auto-download from Hugging Face on first load.
- **faster-whisper ships NO `py.typed`**: a static `from faster_whisper import WhisperModel` triggers basedpyright `reportMissingTypeStubs` (warning → exit 1). The working boundary is `importlib.import_module("faster_whisper")` (no static import at all) + `cast(Callable[..., object], cast(object, getattr(module, "WhisperModel")))` — the `cast(object, ...)` bridge silences reportAny on the Any value, and `import_module` never trips the stub check.
- **ruff B009 (constant `getattr`) vs basedpyright**: `getattr(module, "WhisperModel")` is B009 (use attribute access), but `module.WhisperModel` on a `ModuleType` is a basedpyright attribute-access error — a genuine lint conflict. Resolution: keep `getattr` + `# noqa: B009` with a one-line justification ("untyped boundary"). (Alternative: a module-level `_ATTR = "WhisperModel"` name also dodges B009 since it only flags string literals.)
- **ImportError-before-Exception ordering matters**: `_load_model` catches `ImportError` FIRST → `MissingDependencyError` (pip/uv remediation), then generic `Exception` → `AsrError` with one-time-download remediation. A `ModuleNotFoundError` from the factory is an `ImportError` subclass — ordering decides the typed error.
- **Empty-media check is a pure file-level precondition** (zero-byte `stat().st_size`), so it fires before the model is ever built — the fake-model test asserts `fake.calls == []` proving the model was never touched. Empty-segments and non-monotonic checks live in `_materialize` after the generator is consumed.
- **Materialization test data trap**: `[(0.0, 2.0), (1.5, 3.0)]` is *monotonic in starts* (0.0→1.5) — just overlapping — and does NOT raise. Real non-monotonic data must have a later segment starting earlier: `[(2.0, 4.0), (0.5, 3.0)]` → `0.5 < 2.0` → AsrError.
- 249 pure LOC in `asr.py`: right at the 250 advisory ceiling (one noun phrase + 3 protocol seams + `diagnose()` preflight), no exception needed; 22 hermetic faked-model tests keep the suite at 202.

## Todo 3 (2026-08-07) — reliable selected-format media acquisition

- **basedpyright exits 1 on warnings, not just errors** — "0 errors, 1 warning" still fails CI-style gates. Also, piping `cmd 2>&1 | tail` masks the real exit code (you get `tail`'s); redirect to a file first, then echo `$?`.
- **`reportAny`/`reportExplicitAny` are on by default in basedpyright** (no config needed): `cast(Any, ...)` and `dict[str, Any]` annotations both error. The working boundary pattern is `cast(SomeTypedDict, cast(object, raw))` — `reportInvalidCast` demands an `object` bridge for both directions (dict→TypedDict AND class→Protocol).
- **yt-dlp's typeshed stub (`typeshed-fallback/stubs/yt-dlp`) types `YoutubeDL.__init__(params: _Params | None)`** where `_Params` is a `@type_check_only` private TypedDict, and `extract_info() -> _InfoDict` (also a TypedDict). Passing `dict[str, object]` errors; the stub's `skip_download: str | None` is wrong for real yt-dlp (accepts `True`), so building options as `dict[str, object]` + one boundary cast is correct. Importing `_Params` trips `reportPrivateUsage` (warning → exit 1) → needs `# pyright: ignore[reportPrivateUsage]` with a one-line justification comment; aliasing (`import _Params as X`) does NOT silence it.
- **MP3 outtmpl must NOT end in `.mp3`**: yt-dlp sees a recognized media extension in `outtmpl` as "file already final" and silently skips `FFmpegExtractAudio` postprocessing. Use `destination.with_suffix("")` as outtmpl and recover the real `.mp3` from `requested_downloads[-1]["filepath"]` after the run.
- **Final-path capture = `requested_downloads[-1]["filepath"]` + `Path.is_file()` gate**: `filepath` is set during process/postprocess and IS the real postprocessed path (verified in `process_video_result`, YoutubeDL.py ~3151). Never emit a success `MediaResult` unless the recovered path exists on disk — that single gate covers "no file", "empty path", and "stale/wrong path" in one error branch.
- **Retry design**: 3 attempts, retry ONLY string-marked transient failures (connection reset, timeout, fragment download, HTTP 429/5xx, "rate limit", "too many requests", "temporarily") with `sleep(1.0)`; any unmarked error raises immediately (no busy loop, no masked permanent failures). Unsupported-URL markers map to `InvalidURLError`; everything else to `MediaDownloadError` with remediation text.
- **ffmpeg check precedes every yt-dlp call in `download()`** (both MP3 extract-audio and MP4 merge need it): `MissingDependencyError` with install remediation. A postprocessing ffmpeg failure WITH ffmpeg present is a `MediaDownloadError`, not a misdiagnosed missing dependency — tests assert exactly that split.
- **`Path.write_bytes` does not create parent directories** — tests writing `tmp_path / "T [abc123]/T [abc123].mp4"` failed with `FileNotFoundError` until a `touch()` helper (`parent.mkdir(parents=True, exist_ok=True)` + `write_bytes`) was added; files at `tmp_path / "name.mp4"` (parent exists) were always fine.
- **Format strings**: MP4 = `bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b` (explicit compatible-stream merge, `merge_output_format="mp4"`); MP3 = `ba/b` + `FFmpegExtractAudio` postprocessor with `preferredcodec: "mp3"`, `preferredquality: "192"`. Verified `FFmpegExtractAudioPP._quality_args`: value > 10 → kbps (`-b:a 192k`).
- **`extract_metadata` and `download` both run through the retry loop** (`download=False` for metadata — zero network media bytes; `download=True` for the media), so a transient metadata failure retries too; test seam asserts the call log is `[False, True]` per successful download.

## Todo 7 (2026-08-07) — workflow transaction facts

- The real `WhisperAsrTranscriber` exposes the startup probe as module-level `asr.diagnose()`, not an instance method. The workflow therefore injects a zero-argument diagnosis callable, defaulting to the real probe; tests pass a fake callable and never import or load a model.
- Metadata preflight must happen before `artifact_paths(...)`: the workflow uses the extracted video id/title to derive the safe ID-bearing directory, then passes the exact media path to the injected downloader.
- The existing caption fetcher writes its own transcript, but the workflow writes the returned normalized `TranscriptResult` again so any injected caption service must satisfy the same `transcript.md` artifact contract. Summary services likewise return `SummaryResult`; the workflow owns `summary.md` persistence.
- Partial metadata is written only after media succeeds and transcript/summary work fails. Media errors propagate before any metadata is written, avoiding a false completed or partial transaction for a failed media acquisition.
- `metadata.json` records the four artifact paths, request settings, transcript provenance, model identifiers, status, completed stage, retained paths, and errors. Atomic JSON writes use same-directory temp files, `indent=2`, `sort_keys=True`, `os.replace`, and cleanup on failure.

## Todo 8 (2026-08-07) — interactive CLI facts

- **`typing.cast` evaluates its first argument at runtime** — `cast(YdlParams, ...)` with a `TYPE_CHECKING`-only import raises `NameError` when the line actually executes. media.py's `_default_ydl_factory` has this latent bug (verified: calling it → `NameError: name 'YdlParams' is not defined`; yt_dlp has no runtime `_Params`). The CLI's factory avoids it with a STRING forward-ref cast: `cast("YdlParams", cast(object, dict(options)))` — the string literal evaluates safely at runtime and basedpyright still resolves it. Flag media.py's factory for the final review wave.
- **Wiring the real `YtDlpCaptionFetcher` requires a `transcript_path` that is vestigial in the workflow context** (the workflow re-writes the canonical `transcript.md` from the returned `TranscriptResult`, so the fetcher's own write is a redundant copy). Production wiring passes `Path(tempfile.mkdtemp(prefix="youtube-downloader-captions-")) / "transcript.md"` as disposable scratch — keeps stray files out of `downloads/`; /tmp is OS-cleaned.
- **CLI failure classes map to whether workflow metadata exists**: preflight/media failures (`MissingDependencyError`, `MediaDownloadError`, `InvalidURLError`, `RuntimeError`) occur before any `metadata.json` is written, so the CLI just prints the error; transcript/summary failures (`AsrError`, `TranscriptError`, `SummaryError`, `OSError`) are partial transactions with a written `metadata.json` — the CLI scans `output_root.rglob("metadata.json")` for the most recent `status == "partial"` payload and prints its `retained_paths` verbatim. `except` order in main() is load-bearing: validation tuple, then partial tuple, then generic `DownloaderError`.
- **`reportUnusedCallResult` fires on `validate_url(url)`** (returns `str`, unused) — assign to `_` (same as Todo 1's argparse lesson). Narrowing `object` → `list` in `_retained_paths` leaked `reportUnknownVariableType` on the loop variable; the Todo 6 fix applies here too: `cast(list[object], raw)` after the `isinstance` check.
- **`input()` raises `EOFError` on piped/closed stdin, not `KeyboardInterrupt`** — a pipe with too few lines tracebacked until EOFError was added to the interrupt handler (`except (KeyboardInterrupt, EOFError)` → exit 130, "Interrupted; the session was cancelled."). Also: the cancel message must NOT contain "complete(d)" — tests assert no completion claim via a naive substring check, and "no download was completed" collided with it.
- **console-script/main entry points**: `raise SystemExit(main())` is required (not bare `main()`) so the int exit code propagates; both launchers stay at 4 lines. `main(argv=...)` must be passed `argv=[]` explicitly in tests — with `argv=None` argparse reads pytest's own `sys.argv` and exits 2 on unrecognized arguments.
- **`_default_workflow` keeps service imports inside the factory function**, not at module level — matches the lazy-boundary style (asr/media/captions/summary all defer external imports anyway, but the CLI shouldn't even import the module code at import time). 213 pure LOC in cli.py: one noun phrase (interactive session + failure reporting), all private helpers serve `main()`.

## Todo 9 (2026-08-08) — local setup and boundary documentation

- The README now documents the actual launch paths (`python main.py` and `python -m youtube_downloader`), the required `.venv/bin/uv sync --all-groups` bootstrap, and `ffmpeg -version` preflight with platform-specific remediation.
- Local-only processing must be stated narrowly: YouTube remains a network dependency for metadata, media, and captions; only caption fallback ASR and Ollama summarization are local.
- The runtime defaults are `small` faster-whisper on `cpu`/`int8` and loopback Ollama at `http://127.0.0.1:11434` with model `llama3.2`; first ASR use downloads and caches the model from Hugging Face, so documentation calls out time and disk impact.
- Output documentation follows `artifact_paths()`: `downloads/<safe-title> [<youtube-id>]/`, one `.mp3` or `.mp4`, `transcript.md`, `summary.md`, and `metadata.json`. Complete and partial status behavior is derived from the workflow's retained paths.
- `tests/test_documentation.py` checks required commands, remedies, artifact names, recovery terms, and absence of API-key/cloud-provider setup instructions. Verified with 8 focused tests, 228 full-suite tests, Ruff, and basedpyright.

## Todo 10 (2026-08-08) - quality gate evidence

- The complete chained gate is green after Ruff formatted eight pre-existing files: 229 tests passed, basedpyright reported 0 errors/warnings/notes, and total coverage is 91% (1003 statements, 95 missed).
- `_default_ydl_factory` had a real runtime defect: `cast(YdlParams, ...)` evaluated a TYPE_CHECKING-only name and raised `NameError`. The established string forward-reference cast pattern fixes it; a patched-yt-dlp regression test locks the behavior without network access.
- Normal tests use injected fake yt-dlp, caption, Whisper, and Ollama boundaries. `pytest -m live` selected 0 of 229 tests and made no external calls; its exit code 5 is pytest's expected no-tests-selected status.

## Final Wave F1 (2026-08-08) — completion requires declared artifacts

- The workflow must validate both `MediaResult.final_path` and the declared `ArtifactPaths.media` immediately after the media service returns; an injected service can violate the media adapter's on-disk success contract even when it returns a `MediaResult`.
- Completion is a transaction boundary, not just a stage label: media, transcript, and summary must exist before writing `status: complete`, and the report must only return after the atomic metadata write leaves all four declared artifacts present. The metadata path must not be treated as an existing artifact before that write.

## Final Wave F2 (2026-08-08) — untrusted payload narrowing

- External caption, media, HTTP JSON, and partial-metadata boundaries must validate container and nested entry shapes before applying typed casts; malformed captions remain an unavailable-caption signal, while media and summary failures preserve their typed remediation contracts.
