# local-youtube-downloader - Work Plan

## TL;DR (For humans)

**What you'll get:** A local terminal downloader that asks for a YouTube link, lets you choose MP3 or MP4, saves a transcript for every successful download, and creates a summary tailored to what you ask for.

**Why this approach:** It uses YouTube captions where available, otherwise transcribes locally, then sends only local text to a locally running AI model. No cloud AI account or API key is required.

**What it will NOT do:** It will not provide a web interface, download playlists in bulk, bypass restricted videos, or send transcripts to a cloud AI service.

**Effort:** Medium
**Risk:** Medium - YouTube availability changes and local transcription/summarization require installed binaries and model files.
**Decisions to sanity-check:** Keep the terminal interface; use caption-first, local-ASR fallback; use local Ollama for goal-directed summaries; add pytest tests after implementation.

Your next move: run the plan in a worker session with `$start-work local-youtube-downloader`. Full execution detail follows below.

---

> TL;DR (machine): Medium effort and risk; replace the single script with a tested local CLI for MP3/MP4, transcript generation, and local custom summaries.

## Scope
### Must have
- A single-video interactive CLI that validates a YouTube URL, prompts for MP3 or MP4, and accepts an optional summary goal.
- Distinct per-video output directories containing the selected media, `transcript.md`, `summary.md`, and `metadata.json`.
- Caption-first transcript acquisition; local `faster-whisper` fallback when captions are unavailable or unusable.
- A local-only goal-directed summary through a running Ollama instance; actionable checks and errors for missing local dependencies.
- Automated test coverage without live YouTube, real ASR, or real LLM calls in the normal test suite.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No API keys, cloud LLM provider, telemetry, web server, playlist/channel batch flow, automatic cookie harvesting, DRM bypass, or restriction bypass.
- Do not claim a download succeeded before its transcript and summary artifacts are persisted.
- Do not hard-code titles as unique filenames; do not silently fall back from a requested format.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + pytest, `pytest-mock`, and hermetic temp-directory fixtures.
- Static checks: `uv run ruff check .`, `uv run ruff format --check .`, and `uv run basedpyright`.
- Test command: `uv run pytest --cov=youtube_downloader --cov-report=term-missing`.
- Evidence: `.omo/evidence/task-<N>-local-youtube-downloader.txt` for each task and `.omo/evidence/final-<N>-local-youtube-downloader.txt` for final checks.
- Normal tests must fake yt-dlp, the caption fetcher, Whisper, and Ollama. One separately marked smoke test may run only when all local dependencies and an explicitly supplied public test URL are available.

## Execution strategy
### Parallel execution waves
- Wave 1 establishes packaging, typed boundaries, storage conventions, media transfer, and transcript resolution.
- Wave 2 builds summarization, wires the CLI transaction flow, completes documentation, and runs the full verification wave.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2, 3, 4, 5, 6, 7, 8, 9, 10 | none |
| 2 | 1 | 3, 4, 5, 6, 7 | 3 |
| 3 | 1, 2 | 4, 5, 7 | 2 |
| 4 | 1, 2 | 5, 7 | 3 |
| 5 | 2, 3, 4 | 7 | 6 |
| 6 | 1, 2 | 7 | 5 |
| 7 | 2, 3, 4, 5, 6 | 8, 9, 10 | none |
| 8 | 7 | 10 | 9 |
| 9 | 1, 7 | 10 | 8 |
| 10 | 7, 8, 9 | final wave | none |

## Todos
- [x] 1. Establish the Python project contract and development tooling
  What to do / Must NOT do: Add `pyproject.toml`, `src/youtube_downloader/`, `tests/`, and `README.md`; configure Python 3.14-compatible dependencies, pytest, ruff, basedpyright, and uv. Replace `main.py` with a compatibility launcher of at most 10 lines which delegates to the package CLI; add `src/youtube_downloader/__main__.py` with the same at-most-10-line `if __name__ == "__main__"` launcher. Replace the empty `.gitignore` with exactly scoped entries for `downloads/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.omo/evidence/`, `*.egg-info/`, `dist/`, and `build/`. Do not retain unmanaged dependency installation instructions or commit virtual environments, models, downloads, or generated evidence.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 2, 3, 4, 5, 6, 7, 8, 9, 10
  References (executor has NO interview context - be exhaustive): `main.py:1-32`; empty `.gitignore`; `.venv/pyvenv.cfg:1-5`; `yt-dlp` metadata `METADATA:1-69` confirms Python >=3.10.
  Acceptance criteria (agent-executable): `uv sync --all-groups`, `uv run pytest --collect-only`, `uv run ruff check .`, and `uv run basedpyright` exit 0 from a clean checkout; `uv run python main.py --help` and `uv run python -m youtube_downloader --help` expose the same command help.
  QA scenarios (name the exact tool + invocation): happy: run the six acceptance commands and save their output; failure: add a pytest assertion that `.gitignore` contains every exact required entry and no unsupported broad ignore, then run `uv run pytest tests/test_project_files.py`. Evidence `.omo/evidence/task-1-local-youtube-downloader.txt`.
  Commit: Y | `chore(project): establish typed Python tooling`
- [x] 2. Define typed domain models, errors, settings, and artifact layout
  What to do / Must NOT do: Create typed immutable request/result/error models and Python `Protocol` classes for media download, caption fetch, ASR transcription, and summary generation services; implement validated settings for output root, Whisper model/device/compute type, and Ollama endpoint/model. Permit only loopback Ollama endpoints (`http://127.0.0.1:11434` by default or equivalent `localhost`/`::1`); reject every remote host and all user-info/credential-bearing URLs. Define `<output-root>/<safe-title> [<youtube-id>]/` and the exact media, transcript, summary, and metadata filenames. Do not expose secrets or accept API keys.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 4, 5, 6, 7
  References (executor has NO interview context - be exhaustive): `main.py:4-19` existing output behavior; yt-dlp installed metadata `:731-745,1579-1582` for output templates; user decision recorded in `.omo/drafts/local-youtube-downloader.md` under Decisions.
  Acceptance criteria (agent-executable): unit tests prove invalid URLs/formats/settings return typed errors; valid IDs produce a directory and artifact paths without path traversal or filename collisions.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_models.py tests/test_paths.py`; failure: include `../`, blank URL, unsupported scheme, invalid format, and malformed endpoint test cases. Evidence `.omo/evidence/task-2-local-youtube-downloader.txt`.
  Commit: Y | `feat(core): define downloader domain contracts`
- [x] 3. Implement reliable selected-format media acquisition
  What to do / Must NOT do: Wrap `yt_dlp.YoutubeDL` behind the media protocol; extract metadata before download; map MP4 to explicit compatible-stream merge options and MP3 to `FFmpegExtractAudio` at the documented quality; configure a bounded three-attempt retry for transient download/fragment failures without busy looping; recover the final postprocessed path through yt-dlp metadata/progress state; map download/conversion failures to actionable typed errors. Do not use `ignoreerrors=True`, title-only names, an implicit media format, or restriction-bypass options.
  Parallelization: Wave 1 | Blocked by: 1, 2 | Blocks: 4, 5, 7
  References (executor has NO interview context - be exhaustive): `main.py:14-24`; installed yt-dlp metadata `:280-288,964-1028,2143-2164,2181-2197`; official yt-dlp README dependencies and embedding examples.
  Acceptance criteria (agent-executable): mocked yt-dlp tests assert distinct MP3 and MP4 option maps, an ID-bearing output template, final-path capture, and stable errors for unsupported URL, nonzero return, and missing FFmpeg.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_media.py`; failure: fake `DownloadError`, false FFmpeg diagnostic, and a missing final file, asserting no success result is emitted. Evidence `.omo/evidence/task-3-local-youtube-downloader.txt`.
  Commit: Y | `feat(media): add explicit MP3 and MP4 downloads`
- [x] 4. Implement caption-first transcript resolution and normalized transcript writing
  What to do / Must NOT do: Inspect manual captions first, then automatic captions, excluding live chat; fetch WebVTT, remove HTML/positioning cues, and normalize every cue to `start_seconds`, `end_seconds`, and text; label source, language, and timestamps; write `transcript.md` atomically. Do not treat the absence of subtitles as success or mix subtitle markup into summary input.
  Parallelization: Wave 1 | Blocked by: 1, 2 | Blocks: 5, 7
  References (executor has NO interview context - be exhaustive): yt-dlp installed metadata `:970-982,1579-1582,2397-2404`; librarian research receipt for `bg_ad94b942`; target artifact contract in Todo 2.
  Acceptance criteria (agent-executable): fixture tests prove manual captions win over automatic captions, automatic captions are used when manual are absent, VTT markup is removed, segments are time-ordered, and metadata records source/language.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_captions.py`; failure: no selected language, malformed/empty VTT, and a live-chat-only track each return the precise non-success outcome required to invoke ASR. Evidence `.omo/evidence/task-4-local-youtube-downloader.txt`.
  Commit: Y | `feat(transcript): add caption transcript acquisition`
- [x] 5. Add local faster-whisper transcription fallback
  What to do / Must NOT do: Implement the ASR protocol using `faster-whisper` with configurable model path/name; default to `device="cpu"`, `compute_type="int8"`, automatic language detection, and materialized timestamped segments. Make startup diagnostics verify the installed package can import under the supported Python before any download begins; when a required ASR model is not cached, explain the one-time local model download and preserve the media with `metadata.json.status="partial"` if loading/downloading fails. Invoke it only after caption resolution fails; write the same normalized transcript contract and preserve ASR model/language metadata. Do not require an API key or make real model inference part of the normal tests.
  Parallelization: Wave 1 | Blocked by: 2, 3, 4 | Blocks: 7
  References (executor has NO interview context - be exhaustive): faster-whisper official README requirements, CPU INT8 example, generator warning, and timestamp example; `.omo/drafts/local-youtube-downloader.md` Decisions; Todo 4 normalized-segment contract.
  Acceptance criteria (agent-executable): `uv run python -c "from faster_whisper import WhisperModel; print('OK')"` succeeds in the supported environment; faked model tests prove ASR is skipped when usable captions exist, invoked exactly once when captions are absent, materializes the returned segment generator, and rejects empty/non-monotonic output.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_asr.py`; failure: fake missing model, model-load error, empty audio, empty segments, and non-monotonic timestamps, each yielding a transcript-stage error with remediation. Evidence `.omo/evidence/task-5-local-youtube-downloader.txt`.
  Commit: Y | `feat(transcript): add local speech-to-text fallback`
- [x] 6. Implement the local Ollama summary service
  What to do / Must NOT do: Before media work begins, request `GET {loopback-endpoint}/api/tags` with a finite timeout; require a 200 response and an exact configured model-name match, otherwise show the exact local `ollama serve`/`ollama pull <model>` remediation. Send a deterministic prompt that delimit-transcript data, includes the user’s optional summary goal, requests a concise grounded result, and writes `summary.md` atomically with model/source metadata. Chunk and reduce long transcripts deterministically. Do not call cloud endpoints, read API-key environment variables, execute transcript text as instructions, or present an ungrounded summary as a transcript substitute.
  Parallelization: Wave 1 | Blocked by: 1, 2 | Blocks: 7
  References (executor has NO interview context - be exhaustive): user local-only decision in `.omo/drafts/local-youtube-downloader.md`; Ollama official Generate API and Python-client docs; Hugging Face summarization docs on sequence input length; Todo 2 artifact contract.
  Acceptance criteria (agent-executable): mock HTTP tests prove local endpoint validation, custom-goal propagation, deterministic chunk ordering/reduction, `summary.md` persistence, and no request includes credentials or a non-local configured host.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_summary.py`; failure: unreachable endpoint, missing model, invalid response, oversized transcript, and prompt-injection-like transcript text all produce safe, actionable results. Evidence `.omo/evidence/task-6-local-youtube-downloader.txt`.
  Commit: Y | `feat(summary): add local goal-directed summaries`
- [x] 7. Orchestrate the all-artifact download transaction
  What to do / Must NOT do: Compose diagnostics, metadata extraction, media download, caption/ASR resolution, local summary, atomic sidecar writes, and a final completion report. Persist `metadata.json` with stable ID, request settings, output paths, transcript source/language, model identifiers, `status` (`complete` or `partial`), completed stage, and retained artifact paths. On transcript or summary failure after media exists, retain the media and every valid earlier artifact, write `status="partial"`, and print a warning naming the error plus exact retained paths. Do not print “complete” until all four artifacts exist and do not delete a partially completed download.
  Parallelization: Wave 2 | Blocked by: 2, 3, 4, 5, 6 | Blocks: 8, 9, 10
  References (executor has NO interview context - be exhaustive): `main.py:21-28` broad current error handling; Todo 2 models/layout; Todos 3-6 protocol contracts; `.omo/drafts/local-youtube-downloader.md` Decision that successful download is the transaction boundary.
  Acceptance criteria (agent-executable): orchestration tests cover manual captions, automatic captions, ASR fallback, MP3 and MP4; each ends with media + `transcript.md` + `summary.md` + `metadata.json` and an accurate terminal report.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_workflow.py`; failure: make each stage fail in turn and assert no false success, no partial metadata marked complete, and prior valid artifacts are accurately listed. Evidence `.omo/evidence/task-7-local-youtube-downloader.txt`.
  Commit: Y | `feat(workflow): require transcript and summary artifacts`
- [x] 8. Deliver a clear interactive CLI and compatibility entry point
  What to do / Must NOT do: Implement the typed CLI module and make the at-most-10-line `main.py` and `src/youtube_downloader/__main__.py` launchers only import and invoke its `main()` function. Prompt for URL, explicit MP3/MP4 decision, optional custom summary goal, and show preflight failures plus final artifact paths/status. Ensure Ctrl-C exits cleanly. Do not add a GUI/web server, process multiple URLs, or use `input()` while importing modules.
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 10
  References (executor has NO interview context - be exhaustive): `main.py:30-32`; Todo 7 workflow result contract; `.omo/drafts/local-youtube-downloader.md` interface decision.
  Acceptance criteria (agent-executable): CLI tests simulate an MP3 and MP4 session and assert the exact request passed to orchestration, a readable final report, nonzero exit on validation/preflight failure, and no prompts on module import.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_cli.py`; failure: blank/invalid URL, unrecognized format answer, unavailable local model, and KeyboardInterrupt all exit safely without a completed claim. Evidence `.omo/evidence/task-8-local-youtube-downloader.txt`.
  Commit: Y | `feat(cli): add interactive format and summary choices`
- [x] 9. Document local setup, operations, and boundaries
  What to do / Must NOT do: Write a concise README covering uv setup, an `ffmpeg -version` availability check, Ollama installation/start/model pull, first-time Whisper model download and disk-space impact, supported interaction, output artifacts, local-data boundary, and actionable limitation/recovery guidance. Explain that YouTube is still contacted and restricted/unavailable videos cannot be guaranteed. Do not claim offline YouTube downloads, API-key requirements, a specific FFmpeg minimum version not documented by yt-dlp, or successful transcripts for silent/unavailable media.
  Parallelization: Wave 2 | Blocked by: 1, 7 | Blocks: 10
  References (executor has NO interview context - be exhaustive): installed yt-dlp metadata `:280-288`; faster-whisper official README Requirements and model-download behavior; Ollama official local API docs; scope guardrails in this plan.
  Acceptance criteria (agent-executable): a documentation test/check verifies all executable commands and required artifact names appear; a fresh-environment checklist maps every preflight error to a README remedy.
  QA scenarios (name the exact tool + invocation): happy: `uv run pytest tests/test_documentation.py`; failure: test that no API-key or cloud-provider instruction appears and that restricted-video handling is explicitly documented. Evidence `.omo/evidence/task-9-local-youtube-downloader.txt`.
  Commit: Y | `docs: document local downloader setup and limits`
- [x] 10. Run the complete automated quality gate and package the evidence
  What to do / Must NOT do: Run formatting, linting, type checks, the entire test suite, and a no-network normal-test audit; fix only defects found within approved scope. Save raw command outputs and coverage summary under `.omo/evidence/`. Do not add live YouTube, real ASR, or real Ollama calls to the default quality gate.
  Parallelization: Wave 2 | Blocked by: 7, 8, 9 | Blocks: final wave
  References (executor has NO interview context - be exhaustive): Verification strategy in this plan; Todos 1-9 acceptance criteria; `.omo/drafts/local-youtube-downloader.md` Scope OUT.
  Acceptance criteria (agent-executable): `uv run ruff format --check . && uv run ruff check . && uv run basedpyright && uv run pytest --cov=youtube_downloader --cov-report=term-missing` exits 0 and writes retained evidence.
  QA scenarios (name the exact tool + invocation): happy: run the complete chained command; failure: run `uv run pytest -m live` without opt-in and assert it is deselected/skipped rather than silently calling external services. Evidence `.omo/evidence/task-10-local-youtube-downloader.txt`.
  Commit: Y | `test: verify local downloader workflow`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  Acceptance criteria: independently map every Must-have and Must-NOT-have item to implementation and test evidence; reject any API key, cloud provider, web UI, playlist, restriction-bypass, or false-success behavior.
  QA scenarios: run `uv run pytest` plus inspect `metadata.json` fixture outputs; evidence `.omo/evidence/final-1-local-youtube-downloader.txt`.
- [ ] F2. Code quality review
  Acceptance criteria: `uv run ruff format --check .`, `uv run ruff check .`, and `uv run basedpyright` pass with no ignored diagnostics; review confirms service boundaries make external calls mockable.
  QA scenarios: execute the three commands and retain raw output; evidence `.omo/evidence/final-2-local-youtube-downloader.txt`.
- [ ] F3. Real manual QA
  Acceptance criteria: agent executes the interactive CLI against fakes and verifies MP3 and MP4 journeys create all four expected output artifacts and display their paths.
  QA scenarios: `uv run pytest tests/test_cli.py tests/test_workflow.py -vv`; failure cases include missing FFmpeg, no captions plus ASR error, and unavailable Ollama; evidence `.omo/evidence/final-3-local-youtube-downloader.txt`.
- [ ] F4. Scope fidelity
  Acceptance criteria: inspect the dependency lock, source tree, environment-variable reads, settings validation, and README to prove the release is local-only and has no API-key/cloud/service expansion.
  QA scenarios: run `rg -n -i 'api[_ -]?key|openai|anthropic|os\.environ|environ\.get' src tests README.md pyproject.toml`; run `rg -n 'https?://' src tests README.md pyproject.toml`; manually classify every match, allowing only documentation links, YouTube/yt-dlp traffic, and loopback-only Ollama configuration. Evidence `.omo/evidence/final-4-local-youtube-downloader.txt`.

## Commit strategy
- Commit each completed numbered todo using its specified conventional-commit message; never mix generated downloads, model caches, `.venv/`, or `.omo/evidence/` with product changes.
- Keep the packaging foundation separate from domain contracts, media/transcript services, summary service, orchestration/CLI, documentation, and final test fixes for reviewable history.

## Success criteria
- A local user can complete one terminal session without entering an API key, choose MP3 or MP4, and receive the selected media plus `transcript.md`, `summary.md`, and `metadata.json` in an ID-stable output directory.
- Manual captions are preferred, automatic captions are second, and local ASR is used only when neither usable caption source exists.
- The summary reflects the optional user goal and is produced only through a configured local Ollama endpoint.
- Missing FFmpeg/model/service/captions/ASR/media conditions produce specific failure states and never a false completed download.
- All default automated checks pass without performing live YouTube, real ASR, or real LLM work.
