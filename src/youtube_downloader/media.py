"""Reliable selected-format media acquisition (plan Todo 3).

Wraps ``yt_dlp.YoutubeDL`` behind the :class:`MediaDownloader` protocol:

* metadata is extracted before any media is downloaded;
* MP4 requests map to explicit compatible-stream merge options and MP3
  requests to an ``FFmpegExtractAudio`` postprocessor at the documented
  quality (``192`` kbps for mp3);
* transient download/fragment failures are retried at most three times with
  a small delay between attempts (never a busy loop); permanent failures
  surface immediately;
* the real postprocessed file path is recovered from yt-dlp download state
  (``requested_downloads``), never guessed from the output template;
* every failure maps to a typed, actionable error.

Deliberately absent: ``ignoreerrors``, restriction-bypass options, cookie
harvesting, playlist handling, and title-only output names. yt-dlp itself is
never imported at module level -- the surface is duck-typed (:class:`MediaYdl`)
so tests substitute a fake without network access.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol, cast, final

if TYPE_CHECKING:
    # yt-dlp's typeshed stub exposes only a private TypedDict for its params;
    # it is the exact contract we adapt to at this boundary.
    from yt_dlp import _Params as YdlParams  # pyright: ignore[reportPrivateUsage]

from youtube_downloader.models import (
    DownloadRequest,
    Format,
    InvalidURLError,
    MediaDownloadError,
    MediaResult,
    MissingDependencyError,
)

_MP4_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
_MP3_FORMAT = "ba/b"
_MP3_QUALITY = "192"
_MAX_DOWNLOAD_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0

_FFMPEG_REMEDIATION = (
    "FFmpeg (with ffprobe) is required to merge video+audio into MP4 and to "
    "extract MP3 audio, but no ffmpeg executable was found on PATH. Install "
    "it and retry: on Debian/Ubuntu run 'sudo apt install ffmpeg', on macOS "
    "run 'brew install ffmpeg', or see https://ffmpeg.org/download.html"
)

_TRANSIENT_MARKERS = (
    "unable to download video data",
    "request timed out",
    "timed out",
    "connection",
    "network",
    "fragment",
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "rate limit",
    "temporarily",
    "too many requests",
)

_UNSUPPORTED_URL_MARKERS = (
    "unsupported url",
    "no suitable extractor",
    "is not a valid url",
    "unknown url type",
)


class MediaYdl(Protocol):
    """Minimal context-managed yt-dlp surface the media downloader requires.

    Mirrors the untyped third-party ``yt_dlp.YoutubeDL`` surface: metadata is
    extracted with ``download=False`` and media with ``download=True``, and the
    object is used as a context manager so its request director is always closed.
    """

    def __enter__(self) -> MediaYdl: ...

    def __exit__(self, exc_type: object, exc_value: object, exc_tb: object) -> None: ...

    def extract_info(self, url: str, download: bool = True) -> dict[str, object] | None: ...


def _default_ydl_factory(options: Mapping[str, object]) -> MediaYdl:
    """Build a real ``yt_dlp.YoutubeDL``; imported lazily to keep the module import-free."""
    from yt_dlp import YoutubeDL

    params = cast(YdlParams, cast(object, dict(options)))
    return cast(MediaYdl, cast(object, YoutubeDL(params)))


def _ffmpeg_available() -> bool:
    """True when an ``ffmpeg`` executable is discoverable on PATH."""
    return shutil.which("ffmpeg") is not None


def _metadata_options() -> dict[str, object]:
    return {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }


def _build_options(fmt: Format, destination: Path) -> dict[str, object]:
    """Return the explicit yt-dlp options that end the media at *destination*.

    The output template is ID-bearing and resolves exactly to *destination*:
    MP4 uses the literal path (``<title> [<id>].mp4``); MP3 points at the
    extension-less stem because yt-dlp downloads the original stream to
    ``<stem>.<orig-ext>`` and ``FFmpegExtractAudio`` rewrites it to
    ``<stem>.mp3`` -- an outtmpl ending in ``.mp3`` would make yt-dlp treat
    the source as already-mp3 and skip conversion.
    """
    common: dict[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    match fmt:
        case Format.MP4:
            return {
                **common,
                "format": _MP4_FORMAT,
                "merge_output_format": "mp4",
                "outtmpl": str(destination),
            }
        case Format.MP3:
            return {
                **common,
                "format": _MP3_FORMAT,
                "outtmpl": str(destination.with_suffix("")),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": _MP3_QUALITY,
                    }
                ],
            }


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_optional_float(value: object) -> float | None:
    return value if isinstance(value, (int, float)) else None


def _as_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _media_result(metadata: dict[str, object], final_path: Path) -> MediaResult:
    """Build the typed result from the pre-download metadata and the real path."""
    video_id = _as_optional_str(metadata.get("id")) or ""
    return MediaResult(
        final_path=final_path,
        video_id=video_id,
        title=_as_optional_str(metadata.get("title")) or video_id,
        uploader=_as_optional_str(metadata.get("uploader")),
        duration_seconds=_as_optional_float(metadata.get("duration")),
        upload_date=_as_optional_str(metadata.get("upload_date")),
        view_count=_as_optional_int(metadata.get("view_count")),
        description=_as_optional_str(metadata.get("description")),
    )


def _is_transient(exc: Exception) -> bool:
    """True when *exc* looks like a retryable network/fragment/rate-limit failure."""
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _media_error_remediation(url: str, message: str) -> str:
    lowered = message.lower()
    if "unavailable" in lowered or "private" in lowered or "members only" in lowered:
        return (
            f"Could not download {url!r}: {message}. The video is unavailable, "
            "private, or restricted; this downloader does not bypass restrictions."
        )
    if "sign in" in lowered or "bot" in lowered:
        return (
            f"Could not download {url!r}: {message}. YouTube is asking for "
            "verification; try a different public video."
        )
    if "requested format is not available" in lowered:
        return (
            f"Could not download {url!r}: {message}. No format matching the "
            "request exists for this video."
        )
    return (
        f"Failed to download media from {url!r}: {message}. Check your network "
        "connection and that the video is publicly available."
    )


def _raise_mapped_error(url: str, exc: Exception) -> NoReturn:
    """Convert an arbitrary yt-dlp exception into the typed error contract."""
    message = str(exc)
    lowered = message.lower()
    if any(marker in lowered for marker in _UNSUPPORTED_URL_MARKERS):
        raise InvalidURLError(f"yt-dlp rejected {url!r}: {message}") from exc
    raise MediaDownloadError(_media_error_remediation(url, message)) from exc


def _recover_final_path(info: dict[str, object], url: str) -> Path:
    """Recover the real postprocessed file path from yt-dlp download state.

    ``requested_downloads`` records the final ``filepath`` of every processed
    format (postprocessors rewrite it when they rename the file); the last
    entry is the requested best format. The path must actually exist on disk:
    a missing file is a failed download, never a success.
    """
    raw_requested = info.get("requested_downloads")
    if not isinstance(raw_requested, list) or not raw_requested:
        raise MediaDownloadError(f"yt-dlp reported no downloaded media for {url!r}")
    requested = cast(list[dict[str, object]], raw_requested)
    last = requested[-1]
    filepath = last.get("filepath")
    if not isinstance(filepath, str) or not filepath:
        raise MediaDownloadError(f"yt-dlp did not report a final media path for {url!r}")
    path = Path(filepath)
    if not path.is_file():
        raise MediaDownloadError(
            f"yt-dlp reported success for {url!r} but the media file "
            + f"{str(path)!r} does not exist on disk"
        )
    return path


@final
class YtDlpMediaDownloader:
    """Downloads media through yt-dlp behind the :class:`MediaDownloader` protocol.

    ``ydl_factory`` must return a context-managed :class:`MediaYdl` (defaults
    to ``yt_dlp.YoutubeDL``); ``sleep`` is the delay used between retry
    attempts; ``ffmpeg_available`` decides whether FFmpeg is on PATH (defaults
    to :func:`_ffmpeg_available`). All three seams are injectable so tests
    never touch the network or a real FFmpeg install.
    """

    _ydl_factory: Callable[[Mapping[str, object]], MediaYdl] | None
    _sleep: Callable[[float], None]
    _ffmpeg_available: Callable[[], bool]

    def __init__(
        self,
        ydl_factory: Callable[[Mapping[str, object]], MediaYdl] | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        ffmpeg_available: Callable[[], bool] | None = None,
    ) -> None:
        self._ydl_factory = ydl_factory
        self._sleep = sleep
        self._ffmpeg_available = (
            ffmpeg_available if ffmpeg_available is not None else _ffmpeg_available
        )

    def extract_metadata(self, url: str) -> dict[str, object]:
        """Validate *url* and return yt-dlp's raw info dict without downloading.

        Raises :class:`InvalidURLError` for unsupported URLs and
        :class:`MediaDownloadError` for other yt-dlp failures. The workflow
        (plan Todo 7) uses this seam to compute the destination directory
        before any media is downloaded.
        """
        try:
            info = self._extract_with_retries(url, _metadata_options(), download=False)
        except Exception as exc:  # yt-dlp is an external boundary
            _raise_mapped_error(url, exc)
        if not isinstance(info, dict):
            raise MediaDownloadError(
                f"yt-dlp returned an unexpected result for {url!r}; the video may be unavailable"
            )
        return info

    def download(self, request: DownloadRequest, destination: Path) -> MediaResult:
        """Download *request* to *destination* and return the real media result.

        FFmpeg is checked before any yt-dlp call (both MP4 merging and MP3
        extraction depend on it). Metadata is extracted first, then the media
        is downloaded with a bounded retry; the final postprocessed path is
        recovered from yt-dlp state and returned alongside the metadata.
        """
        if not self._ffmpeg_available():
            raise MissingDependencyError(_FFMPEG_REMEDIATION)
        metadata = self.extract_metadata(request.url)
        options = _build_options(request.format, destination)
        try:
            info = self._extract_with_retries(request.url, options, download=True)
        except Exception as exc:  # yt-dlp is an external boundary
            _raise_mapped_error(request.url, exc)
        if not isinstance(info, dict):
            raise MediaDownloadError(
                f"yt-dlp returned an unexpected result for {request.url!r}; no media was downloaded"
            )
        return _media_result(metadata, _recover_final_path(info, request.url))

    def _extract_with_retries(
        self, url: str, options: Mapping[str, object], *, download: bool
    ) -> dict[str, object] | None:
        """Run ``extract_info`` with a bounded retry on transient failures.

        At most :data:`_MAX_DOWNLOAD_ATTEMPTS` attempts with a small delay
        between them; only transient (network/fragment/rate-limit) errors are
        retried, so permanent failures surface immediately.
        """
        factory = self._ydl_factory if self._ydl_factory is not None else _default_ydl_factory
        last_error: Exception | None = None
        for attempt in range(1, _MAX_DOWNLOAD_ATTEMPTS + 1):
            try:
                with factory(dict(options)) as ydl:
                    return ydl.extract_info(url, download=download)
            except Exception as exc:  # yt-dlp is an external boundary
                last_error = exc
                if attempt == _MAX_DOWNLOAD_ATTEMPTS or not _is_transient(exc):
                    raise
                self._sleep(_RETRY_DELAY_SECONDS)
        assert last_error is not None
        raise last_error
