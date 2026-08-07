"""Interactive command-line interface for the local downloader (plan Todo 8).

Prompts for one YouTube URL, an explicit MP3/MP4 choice, and an optional
summary goal; validates the URL and format through the existing
:class:`DownloadRequest` and :class:`Format` contracts; then runs the
all-artifact :class:`DownloadWorkflow` transaction against the verified
services and :class:`Settings`.

Nothing is prompted and no service is constructed at import time: prompting,
output, settings, and workflow construction are all injectable seams, so the
tests never contact YouTube, Ollama, faster-whisper, or ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from youtube_downloader.captions import CaptionYdl
from youtube_downloader.models import (
    AsrError,
    DownloaderError,
    DownloadRequest,
    Format,
    InvalidFormatError,
    InvalidURLError,
    SummaryError,
    TranscriptError,
    validate_url,
)
from youtube_downloader.settings import Settings
from youtube_downloader.workflow import DownloadWorkflow

if TYPE_CHECKING:
    # yt-dlp's typeshed stub exposes only a private TypedDict for its params;
    # the string forward-ref cast below adapts to it without naming it at runtime.
    from yt_dlp import _Params as YdlParams  # pyright: ignore[reportPrivateUsage]

_EXIT_OK = 0
_EXIT_SERVICE_FAILURE = 1
_EXIT_VALIDATION_FAILURE = 2
_EXIT_INTERRUPTED = 130

_URL_PROMPT = "YouTube video URL: "
_FORMAT_PROMPT = "Format (mp3 or mp4): "
_GOAL_PROMPT = "Summary goal (optional, press Enter to skip): "

PromptFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]
WorkflowFactory = Callable[[Settings], DownloadWorkflow]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-downloader",
        description=(
            "Interactive local downloader: asks for a YouTube URL, an explicit "
            "MP3 or MP4 choice, and an optional summary goal, then saves the "
            "media plus transcript, summary, and metadata artifacts locally."
        ),
    )
    return parser


def _print_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _ytdl_factory(options: Mapping[str, object]) -> CaptionYdl:
    """Build a context-managed yt-dlp surface (imported lazily at the boundary)."""
    from yt_dlp import YoutubeDL

    params = cast("YdlParams", cast(object, dict(options)))
    return cast(CaptionYdl, cast(object, YoutubeDL(params)))


def _default_workflow(settings: Settings) -> DownloadWorkflow:
    """Build the production :class:`DownloadWorkflow` from *settings*.

    Composes the verified service implementations. The caption fetcher writes
    its own transcript copy to a scratch directory; the workflow writes the
    canonical ``transcript.md`` artifact from the returned result, so the
    scratch copy is disposable (``/tmp`` is cleaned by the OS).
    """
    from youtube_downloader.asr import WhisperAsrTranscriber
    from youtube_downloader.captions import YtDlpCaptionFetcher
    from youtube_downloader.media import YtDlpMediaDownloader
    from youtube_downloader.summary import OllamaSummarizer

    scratch_dir = Path(tempfile.mkdtemp(prefix="youtube-downloader-captions-"))
    captions = YtDlpCaptionFetcher(_ytdl_factory, scratch_dir / "transcript.md")
    return DownloadWorkflow(
        media=YtDlpMediaDownloader(),
        captions=captions,
        asr=WhisperAsrTranscriber(
            model=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        ),
        summarizer=OllamaSummarizer(
            endpoint=settings.ollama_endpoint,
            model=settings.ollama_model,
        ),
        settings=settings,
    )


def _prompt_url(prompt: PromptFunc) -> str:
    return prompt(_URL_PROMPT).strip()


def _prompt_format(prompt: PromptFunc) -> Format:
    return Format.from_str(prompt(_FORMAT_PROMPT))


def _prompt_goal(prompt: PromptFunc) -> str | None:
    goal = prompt(_GOAL_PROMPT).strip()
    return goal or None


def _latest_partial_payload(output_root: Path) -> dict[str, object] | None:
    """Return the most recent ``status == "partial"`` metadata payload, if any.

    The workflow writes ``metadata.json`` with retained artifact paths when a
    transcript or summary stage fails after the media exists; this reader lets
    the CLI surface those exact paths without duplicating the transaction
    logic. Only the most recently modified partial payload is reported.
    """
    if not output_root.is_dir():
        return None
    best: tuple[float, dict[str, object]] | None = None
    for candidate in output_root.rglob("metadata.json"):
        try:
            payload = cast(
                dict[str, object], json.loads(candidate.read_text(encoding="utf-8"))
            )
            modified = candidate.stat().st_mtime
        except (OSError, ValueError):
            continue
        if payload.get("status") != "partial":
            continue
        if best is None or modified > best[0]:
            best = (modified, payload)
    return None if best is None else best[1]


def _retained_paths(payload: dict[str, object]) -> list[str]:
    raw = payload.get("retained_paths")
    if not isinstance(raw, list):
        return []
    typed = cast(list[object], raw)
    return [str(item) for item in typed if isinstance(item, str)]


def _report_partial(exc: BaseException, output_root: Path, error_out: OutputFunc) -> None:
    """Print the failure plus the exact retained artifact paths, if known."""
    error_out(f"error: {exc}")
    payload = _latest_partial_payload(output_root)
    retained = _retained_paths(payload) if payload is not None else []
    if not retained:
        return
    error_out("The download is partial; these artifacts were retained:")
    for path in retained:
        error_out(f"  {path}")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    prompt: PromptFunc = input,
    out: OutputFunc = print,
    error_out: OutputFunc = _print_stderr,
    workflow_factory: WorkflowFactory | None = None,
) -> int:
    """Run one interactive download session and return the process exit code.

    Exit codes: ``0`` complete, ``1`` preflight/service/partial failure,
    ``2`` invalid input, ``130`` interrupted by Ctrl-C or end-of-input. The
    final status and artifact paths are taken verbatim from the workflow
    report, so a download is never claimed complete unless the workflow
    returned it.
    """
    _ = build_parser().parse_args(argv)
    effective_settings = settings if settings is not None else Settings()
    factory = workflow_factory if workflow_factory is not None else _default_workflow
    try:
        url = _prompt_url(prompt)
        _ = validate_url(url)
        fmt = _prompt_format(prompt)
        goal = _prompt_goal(prompt)
        request = DownloadRequest(url, fmt, goal)
        out(f"Downloading {request.url} as {request.format.value} ...")
        report = factory(effective_settings).run(request)
    except (KeyboardInterrupt, EOFError):
        error_out("Interrupted; the session was cancelled.")
        return _EXIT_INTERRUPTED
    except (InvalidURLError, InvalidFormatError) as exc:
        error_out(f"error: {exc}")
        return _EXIT_VALIDATION_FAILURE
    except (AsrError, TranscriptError, SummaryError, OSError) as exc:
        _report_partial(exc, effective_settings.output_root, error_out)
        return _EXIT_SERVICE_FAILURE
    except (DownloaderError, RuntimeError) as exc:
        error_out(f"error: {exc}")
        return _EXIT_SERVICE_FAILURE
    out(report)
    return _EXIT_OK
