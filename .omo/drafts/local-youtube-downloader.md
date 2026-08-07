---
slug: local-youtube-downloader
status: plan-created
intent: clear
review_required: false
pending-action: hand off .omo/plans/local-youtube-downloader.md to a worker session
approach: Replace the single interactive script with a tested local-only Python CLI that downloads a selected MP3 or MP4, obtains captions or local ASR text for every successful download, and uses a locally running Ollama model to create a user-goal-directed summary.
---

# Draft: local-youtube-downloader

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| cli | A terminal session validates one YouTube URL and collects format plus summary goal. | active | `main.py:30-32` |
| media | One selected MP3 or MP4 is saved with a stable, collision-safe output identity. | active | `main.py:14-24`; yt-dlp installed README metadata: `METADATA:964-1028` |
| transcript | Every successful download gets a persisted transcript sourced from captions or local ASR. | active | yt-dlp installed README metadata: `METADATA:970-982`; faster-whisper official README |
| summary | A custom-goal summary is generated without a cloud API or API key. | active | Hugging Face summarization docs; local-only decision recorded 2026-08-07 |
| quality | Setup diagnostics, automated tests, and documentation make local operation reproducible. | active | repository root has no manifest or test suite |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| interface | Keep an interactive terminal CLI rather than add a web interface. | Current app is terminal-only at `main.py:30-32`; a UI is outside the request. | yes |
| summary runtime | Use a locally running Ollama model, configurable via settings, and fail before download when unavailable. | It supports the requested custom summary goal while keeping transcript data local and requiring no API key. | yes |
| ASR baseline | Use `faster-whisper` on CPU with INT8 and configurable model name. | Its official docs support CPU INT8, timestamps, language detection, and PyAV audio decoding. | yes |
| test style | Add tests after implementation with pytest, mocked network/model boundaries, and no live YouTube test. | The repository has no test infrastructure; this is fast and deterministic. | yes |

## Findings (cited - path:lines)
- `main.py:4-29` hard-codes MP4 selection, uses a title-only output template, and catches all exceptions without propagating an actionable result.
- `main.py:30-32` is the only current interface and reads a single URL interactively.
- `.gitignore` is empty; `downloads/` and `.venv/` are currently in the worktree.
- `yt-dlp` documents MP3 extraction via `FFmpegExtractAudio`, MP4 merge selection, and manual/automatic subtitle retrieval in `.venv/lib/python3.14/site-packages/yt_dlp-2026.2.4.dist-info/METADATA:964-1028,2181-2197`.
- `yt-dlp` requires the FFmpeg binary for stream merge and media post-processing: installed metadata `:280-288`.

## Decisions (with rationale)
- Successful media download is the transaction boundary: do not report success until the transcript and summary artifacts are written; report a clear failure state when a local dependency or transcript generation fails.
- Prefer manual captions, then automatic captions, then local ASR; store source and language in metadata.
- Never silently discard missing captions, model errors, or media-conversion errors.
- Keep all inference local. YouTube access remains networked; local model installation/download is a one-time network action.

## Scope IN
- Interactive MP3/MP4 choice, transcript sidecar, custom local summary, local dependency diagnostics, tests, and documentation.

## Scope OUT (Must NOT have)
- Cloud LLM APIs, API-key handling, web UI, batch playlists/channels, DRM bypass, automatic browser-cookie extraction, or bypassing YouTube access restrictions.

## Open questions
- None. The user chose local-only operation and the remaining reversible implementation choices are recorded above.

## Approval gate
status: approved
approach: Build the local-only CLI and artifact pipeline described above; use a worker session for execution.
next workflow action: `$start-work local-youtube-downloader`
