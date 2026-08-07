"""Caption-first transcript resolution and normalized transcript writing.

Resolution order is manual captions, then automatic captions, with live-chat
tracks always excluded. The chosen WebVTT payload is parsed into normalized
:class:`Segment` cues (no markup, time-ordered, empty cues dropped) and written
to ``transcript.md`` atomically.

Error contract (load-bearing for the ASR fallback in the workflow):

* :class:`CaptionsUnavailableError` -- no usable caption track exists (only
  live chat, an empty language key, unusable entries, or a malformed/empty VTT
  payload). This is the signal to fall back to local speech-to-text.
* :class:`TranscriptError` -- a real failure fetching or parsing captions
  (yt-dlp metadata failure, caption-track network failure).

Only the Python standard library is used; yt-dlp itself is never imported here.
The ``yt-dlp`` surface is injected through ``YtDlpCaptionFetcher.ydl_factory``
so tests can substitute a fake without network access.
"""

from __future__ import annotations

import html
import itertools
import os
import re
import tempfile
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, cast

from youtube_downloader.models import (
    CaptionsUnavailableError,
    Segment,
    TranscriptError,
    TranscriptResult,
)

_VIDEO_URL_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"

_DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; youtube-downloader/0.1)"
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_TIMEOUT_SECONDS = 30.0

_SOURCE_LABELS: Mapping[Literal["manual", "auto"], str] = {
    "manual": "manual_captions",
    "auto": "auto_captions",
}

_TRACK_SOURCES: tuple[tuple[Literal["manual", "auto"], str], ...] = (
    ("manual", "subtitles"),
    ("auto", "automatic_captions"),
)
_TAG_PATTERN = re.compile(r"<[^>]*>")
_FULL_TIMESTAMP = re.compile(r"^(?P<h>\d{1,3}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})$")
_MINUTE_TIMESTAMP = re.compile(r"^(?P<m>\d{1,2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})$")


@dataclass(frozen=True)
class CaptionTrack:
    """One selected caption track before its payload is fetched.

    ``kind`` is ``"manual"`` or ``"auto"``; ``language`` is the code reported
    by yt-dlp (for example ``"en"`` or ``"pt-BR"``). Either ``url`` (download
    the track) or ``data`` (inline payload) must be present.
    """

    kind: Literal["manual", "auto"]
    language: str
    url: str | None
    ext: str | None
    data: str | None


class CaptionYdl(Protocol):
    """Minimal context-managed yt-dlp surface the caption fetcher requires.

    Mirrors the untyped third-party ``yt_dlp.YoutubeDL`` surface: metadata is
    extracted without downloading media, and the object is used as a context
    manager so its request director is always closed.
    """

    def __enter__(self) -> CaptionYdl: ...

    def __exit__(self, exc_type: object, exc_value: object, exc_tb: object) -> None: ...

    def extract_info(self, url: str, download: bool = True) -> dict[str, object] | None: ...


def _as_optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return None


def _tracks(info: Mapping[str, object], key: str) -> dict[str, list[dict[str, object]]]:
    """Extract and type-claim the ``subtitles``/``automatic_captions`` mapping.

    yt-dlp metadata is untyped external data; the cast is the
    parse-at-the-boundary step that reduces it to a well-typed structure.
    """
    raw = info.get(key)
    if not isinstance(raw, dict):
        return {}
    return cast(dict[str, list[dict[str, object]]], raw)


def _is_live_chat(language: str) -> bool:
    """True for yt-dlp live-chat/danmaku pseudo-subtitle tracks."""
    return (
        language == "live_chat"
        or language.startswith("live_chat")
        or language.startswith("danmaku")
    )


def _best_entry(entries: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """Pick the most usable format entry: WebVTT preferred, best fallback last."""
    usable = [
        entry for entry in entries if entry.get("url") is not None or entry.get("data") is not None
    ]
    if not usable:
        return None
    for entry in usable:
        if entry.get("ext") == "vtt":
            return entry
    return usable[-1]


def select_caption_track(info: Mapping[str, object]) -> CaptionTrack | None:
    """Resolve the best caption track: manual first, then automatic.

    Live-chat tracks and tracks with an empty language key are never selected.
    Returns ``None`` when no usable track exists -- the
    :class:`CaptionsUnavailableError` signal for the caller.
    """
    for kind, key in _TRACK_SOURCES:
        for language, entries in _tracks(info, key).items():
            if not language.strip() or _is_live_chat(language):
                continue
            entry = _best_entry(entries)
            if entry is None:
                continue
            return CaptionTrack(
                kind=kind,
                language=language,
                url=_as_optional_str(entry.get("url")),
                ext=_as_optional_str(entry.get("ext")),
                data=_as_optional_str(entry.get("data")),
            )
    return None


def normalize_language(language: str) -> str:
    """Reduce a yt-dlp language code to its primary subtag.

    ``"en"`` stays ``"en"``; ``"pt-BR"`` and ``"zh-Hans"`` become ``"pt"`` and
    ``"zh"`` -- the ISO 639-1 primary codes.
    """
    return language.split("-", 1)[0]


def clean_vtt_text(text: str) -> str:
    """Strip VTT inline markup from one cue's text and collapse whitespace.

    Tags are removed *before* HTML entities are decoded so that a literal
    ``&lt;c&gt;`` in the text survives as ``<c>`` instead of being eaten as a
    tag. The result is single-spaced and free of cue markup.
    """
    without_tags = _TAG_PATTERN.sub("", text)
    return " ".join(html.unescape(without_tags).split())


def _parse_timestamp(token: str) -> float | None:
    """Parse a WebVTT timestamp (``HH:MM:SS.mmm`` or ``MM:SS.mmm``) to seconds."""
    full = _FULL_TIMESTAMP.match(token)
    if full:
        values = (int(full.group(name)) for name in ("h", "m", "s", "ms"))
        hours, minutes, seconds, fraction = values
    else:
        minute_only = _MINUTE_TIMESTAMP.match(token)
        if minute_only is None:
            return None
        hours, minutes, seconds, fraction = (
            0,
            *(int(minute_only.group(name)) for name in ("m", "s", "ms")),
        )
    if minutes > 59 or seconds > 59:
        return None
    milliseconds = int(str(fraction).ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0


def _cue_times(line: str) -> tuple[float, float] | None:
    """Parse ``start --> end [cue settings]`` into a (start, end) pair.

    Returns ``None`` for lines without a valid, ordered timing arrow.
    """
    if "-->" not in line:
        return None
    start_part, _, rest = line.partition("-->")
    rest = rest.strip()
    end_part = rest.split()[0] if rest else ""
    start = _parse_timestamp(start_part.strip())
    end = _parse_timestamp(end_part)
    if start is None or end is None or end < start:
        return None
    return start, end


def parse_webvtt(vtt_text: str) -> tuple[Segment, ...]:
    """Parse the standard WebVTT subset into normalized, ordered segments.

    Handles the optional ``WEBVTT`` header, ``NOTE`` blocks, optional cue
    identifiers, numeric timestamps with cue settings, and blank-line block
    separators. Inline tags are stripped and empty cues dropped. Malformed cue
    lines are skipped leniently; a payload with no surviving cues returns an
    empty tuple (the caller's :class:`CaptionsUnavailableError` trigger).
    """
    if not vtt_text.strip():
        return ()
    lines = vtt_text.splitlines()
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0][1:]
    index = 0
    if lines and lines[index].strip().upper().startswith("WEBVTT"):
        index += 1
    segments: list[Segment] = []
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if line.upper().startswith("NOTE"):
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        times = _cue_times(line)
        if times is None:
            # Cue identifier line (or stray text) -- the timing line follows.
            continue
        start, end = times
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = clean_vtt_text("\n".join(text_lines))
        if text:
            segments.append(Segment(start_seconds=start, end_seconds=end, text=text))
    return tuple(segments)


def _format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def render_transcript_markdown(result: TranscriptResult, video_id: str) -> str:
    """Render a normalized transcript as markdown with a provenance header."""
    header = (
        "# Transcript\n\n"
        f"Source: {result.source}\n"
        f"Language: {result.language}\n"
        f"Video ID: {video_id}\n"
    )
    cue_lines: list[str] = []
    for segment in result.segments:
        window = (
            f"[{_format_timestamp(segment.start_seconds)} --> "
            f"{_format_timestamp(segment.end_seconds)}]"
        )
        cue_lines.append(f"{window} {segment.text}")
    return "\n".join([header, "", *cue_lines, ""])


def write_transcript_markdown(path: Path, result: TranscriptResult, video_id: str) -> None:
    """Write ``transcript.md`` atomically: temp file in the target dir, then replace."""
    content = render_transcript_markdown(result, video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".transcript-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(content)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class YtDlpCaptionFetcher:
    """Resolves captions through an injected yt-dlp surface and writes the transcript.

    ``ydl_factory`` must return a context-managed :class:`CaptionYdl` whose
    ``extract_info`` returns a metadata dict (no media download). The selected
    WebVTT payload is fetched either from the track's inline ``data`` or from
    its URL with a bounded retry, parsed, validated, written to
    ``transcript_path`` atomically, and returned as a :class:`TranscriptResult`.
    """

    _ydl_factory: Callable[[Mapping[str, object]], CaptionYdl]
    _transcript_path: Path
    _max_attempts: int
    _timeout_seconds: float

    def __init__(
        self,
        ydl_factory: Callable[[Mapping[str, object]], CaptionYdl],
        transcript_path: Path,
        *,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._ydl_factory = ydl_factory
        self._transcript_path = transcript_path
        self._max_attempts = max_attempts
        self._timeout_seconds = timeout_seconds

    def _inspect_options(self) -> dict[str, object]:
        return {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

    def _extract_info(self, video_id: str) -> dict[str, object]:
        url = _VIDEO_URL_TEMPLATE.format(video_id=video_id)
        try:
            with self._ydl_factory(self._inspect_options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except TranscriptError:
            raise
        except Exception as exc:
            # yt-dlp is an external boundary: any of its exceptions becomes a
            # typed TranscriptError with remediation, never a silent success.
            raise TranscriptError(
                f"Failed to fetch caption metadata for video {video_id}: {exc}"
            ) from exc
        if info is None:
            raise TranscriptError(f"yt-dlp returned no metadata for video {video_id}")
        return info

    def _http_get(self, url: str, headers: Mapping[str, str]) -> str:
        request = urllib.request.Request(url, headers=dict(headers))
        last_error: Exception | None = None
        for _attempt in range(self._max_attempts):
            try:
                opener = cast(
                    BinaryIO,
                    urllib.request.urlopen(request, timeout=self._timeout_seconds),
                )
                try:
                    payload = opener.read()
                finally:
                    opener.close()
                return payload.decode("utf-8", errors="replace")
            except Exception as exc:  # network layer: retry, then fail typed
                last_error = exc
        raise TranscriptError(f"Failed to download the caption track: {last_error}")

    def _acquire_vtt(self, track: CaptionTrack, info: Mapping[str, object]) -> str:
        if track.data is not None:
            return track.data
        if track.url is None:
            raise TranscriptError("The selected caption track has neither data nor a URL")
        headers: dict[str, str] = {"User-Agent": _DEFAULT_USER_AGENT}
        info_headers = info.get("http_headers")
        if isinstance(info_headers, Mapping):
            typed_headers = cast(Mapping[str, str], info_headers)
            for name, value in typed_headers.items():
                headers[name] = value
        return self._http_get(track.url, headers)

    def _validate_order(self, segments: Sequence[Segment]) -> None:
        for previous, current in itertools.pairwise(segments):
            if previous.start_seconds > current.start_seconds:
                raise CaptionsUnavailableError(
                    "Caption cues are not time-ordered; no usable captions"
                )

    def fetch(self, video_id: str) -> TranscriptResult:
        info = self._extract_info(video_id)
        track = select_caption_track(info)
        if track is None:
            raise CaptionsUnavailableError(
                f"No usable captions are available for video {video_id} "
                + "(manual and automatic caption tracks are missing or unusable)"
            )
        vtt_text = self._acquire_vtt(track, info)
        segments = parse_webvtt(vtt_text)
        if not segments:
            raise CaptionsUnavailableError(
                f"The caption track for video {video_id} is empty or malformed; "
                + "no usable captions"
            )
        self._validate_order(segments)
        result = TranscriptResult(
            segments=segments,
            source=_SOURCE_LABELS[track.kind],
            language=normalize_language(track.language),
        )
        try:
            write_transcript_markdown(self._transcript_path, result, video_id)
        except OSError as exc:
            raise TranscriptError(
                f"Failed to write transcript to {self._transcript_path}: {exc}"
            ) from exc
        return result
