"""Typed domain models, errors, and service protocols for the downloader.

These are the load-bearing contracts that plan Todos 3-6 implement against:
immutable request/result value objects, a typed error hierarchy with
actionable messages, and the mockable service seams (``Protocol`` classes).
No secrets or API keys appear anywhere in this module.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def validate_url(url: str) -> str:
    """Validate *url* as a non-credential-bearing http(s) address.

    Returns the whitespace-stripped URL, or raises :class:`InvalidURLError`
    when *url* is blank, malformed, uses an unsupported scheme, has no host,
    or embeds user-info credentials (a secret-leak hazard).
    """
    stripped = url.strip()
    if not stripped:
        raise InvalidURLError("URL must not be blank")
    try:
        parsed = urllib.parse.urlsplit(stripped)
    except ValueError as exc:
        raise InvalidURLError(f"URL is malformed: {url!r}") from exc
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise InvalidURLError(
            f"Unsupported URL {url!r}: only http(s) addresses with a host are allowed"
        )
    if parsed.username is not None or parsed.password is not None:
        raise InvalidURLError("URL must not embed user-info credentials")
    return stripped


class DownloaderError(Exception):
    """Base class for every downloader error.

    ``str(error)`` is always a message an operator can act on.
    """

    default_message: str = "Downloader operation failed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)


class InvalidURLError(DownloaderError):
    default_message: str = (
        "The URL must be a non-blank http(s) address without embedded credentials"
    )


class InvalidFormatError(DownloaderError):
    default_message: str = "The format must be exactly 'mp3' or 'mp4'"


class SettingsError(DownloaderError):
    default_message: str = "The downloader settings are invalid"


class MediaDownloadError(DownloaderError):
    default_message: str = "Failed to download the media for this video"


class TranscriptError(DownloaderError):
    default_message: str = "Failed to obtain a transcript for this video"


class CaptionsUnavailableError(TranscriptError):
    default_message: str = (
        "No usable captions are available for this video; local transcription is required"
    )


class AsrError(DownloaderError):
    default_message: str = "Failed to transcribe this video locally"


class SummaryError(DownloaderError):
    default_message: str = "Failed to generate the local summary"


class MissingDependencyError(DownloaderError):
    default_message: str = (
        "A required local dependency is missing (for example FFmpeg, "
        "faster-whisper, or the Ollama service)"
    )


class Format(Enum):
    """Selectable media output formats."""

    MP3 = "mp3"
    MP4 = "mp4"

    @classmethod
    def from_str(cls, value: str) -> Format:
        """Parse a user-supplied format string, case- and space-insensitively.

        Raises :class:`InvalidFormatError` for anything other than mp3/mp4.
        """
        try:
            return cls(value.lower().strip())
        except (ValueError, AttributeError) as exc:
            raise InvalidFormatError(f"Unknown format {value!r}; choose 'mp3' or 'mp4'") from exc


@dataclass(frozen=True)
class DownloadRequest:
    """A validated request to download one video in one format.

    The URL is validated at construction time (parse at the boundary), so an
    invalid request can never be represented.
    """

    url: str
    format: Format
    summary_goal: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", validate_url(self.url))


@dataclass(frozen=True)
class MediaResult:
    """Outcome of a successful media download, plus video metadata.

    ``final_path`` is the real post-processed path (yt-dlp postprocessing may
    differ from the requested destination). The optional fields feed
    ``metadata.json`` and default to ``None`` when yt-dlp did not supply them.
    """

    final_path: Path
    video_id: str
    title: str
    uploader: str | None = None
    duration_seconds: float | None = None
    upload_date: str | None = None
    view_count: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class Segment:
    """One normalized timestamped transcript segment (seconds, text)."""

    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class TranscriptResult:
    """A normalized transcript with its provenance.

    ``source`` is one of ``"manual_captions"``, ``"auto_captions"``, or
    ``"asr:<model>"``; ``language`` is an ISO 639-1 code where known.
    """

    segments: tuple[Segment, ...]
    source: str
    language: str


@dataclass(frozen=True)
class SummaryResult:
    """Outcome of a local goal-directed summary."""

    text: str
    model: str
    source_transcript_path: Path | None = None


@runtime_checkable
class MediaDownloader(Protocol):
    """Downloads media for a request to an exact destination path.

    Implementations must map :class:`Format` to explicit yt-dlp options and
    raise :class:`MediaDownloadError` (with remediation) when the media cannot
    be acquired or converted.
    """

    def download(self, request: DownloadRequest, destination: Path) -> MediaResult: ...


@runtime_checkable
class CaptionFetcher(Protocol):
    """Resolves a normalized transcript from the best available captions.

    Prefer manual captions over automatic captions. Raise
    :class:`CaptionsUnavailableError` when no usable caption track exists so
    the workflow can fall back to local ASR; raise :class:`TranscriptError`
    for other failures.
    """

    def fetch(self, video_id: str) -> TranscriptResult: ...


@runtime_checkable
class AsrTranscriber(Protocol):
    """Transcribes local media into normalized timestamped segments.

    Raise :class:`AsrError` (with remediation) when transcription fails.
    """

    def transcribe(self, media_path: Path) -> TranscriptResult: ...


@runtime_checkable
class Summarizer(Protocol):
    """Summarizes a transcript against an optional user goal.

    Implementations must use only the configured loopback Ollama endpoint and
    raise :class:`SummaryError` (with remediation) on failure.
    """

    def summarize(
        self, transcript: TranscriptResult, summary_goal: str | None
    ) -> SummaryResult: ...
