"""Fixture-based tests for caption-first transcript resolution.

All tests are hermetic: yt-dlp is faked with a context-managed surface that
returns plain dict fixtures (plus inline ``data`` VTT payloads), and the only
network-adjacent path (``YtDlpCaptionFetcher._http_get``) is monkeypatched.
No live YouTube calls happen anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from youtube_downloader.captions import (
    YtDlpCaptionFetcher,
    normalize_language,
    parse_webvtt,
    select_caption_track,
    write_transcript_markdown,
)
from youtube_downloader.models import (
    CaptionsUnavailableError,
    Segment,
    TranscriptError,
    TranscriptResult,
)

MANUAL_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
This is the manual caption text
"""

AUTO_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
This is the automatic caption text
"""

MANUAL_EN_TRACK: dict[str, object] = {
    "url": "https://example.invalid/manual-en.vtt",
    "ext": "vtt",
    "name": "English",
    "data": MANUAL_VTT,
}

AUTO_EN_TRACK: dict[str, object] = {
    "url": "https://example.invalid/auto-en.vtt",
    "ext": "vtt",
    "data": AUTO_VTT,
}


class FakeYdl:
    """Minimal context-managed stand-in for ``yt_dlp.YoutubeDL``."""

    def __init__(self, info: dict[str, object], error: Exception | None = None) -> None:
        self._info = info
        self._error = error

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_tb: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
        if download:
            raise AssertionError("caption fetch must never download media")
        if self._error is not None:
            raise self._error
        return self._info


def make_fetcher(info: dict[str, object], transcript_path: Path) -> YtDlpCaptionFetcher:
    return YtDlpCaptionFetcher(lambda _opts: FakeYdl(info), transcript_path)


def test_manual_captions_win_over_automatic(tmp_path: Path) -> None:
    """Given manual and automatic tracks for the same language, manual is used."""
    info: dict[str, object] = {
        "subtitles": {"en": [MANUAL_EN_TRACK]},
        "automatic_captions": {"en": [AUTO_EN_TRACK]},
    }
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    result = fetcher.fetch("abc123")

    assert result.source == "manual_captions"
    assert result.segments[0].text == "This is the manual caption text"


def test_automatic_captions_used_when_manual_absent(tmp_path: Path) -> None:
    """Given only automatic captions, they are used and labeled as such."""
    info: dict[str, object] = {"automatic_captions": {"en": [AUTO_EN_TRACK]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    result = fetcher.fetch("abc123")

    assert result.source == "auto_captions"
    assert result.segments[0].text == "This is the automatic caption text"


def test_select_caption_track_manual_first() -> None:
    """Resolution prefers manual over automatic and WebVTT over other formats."""
    info: dict[str, object] = {
        "subtitles": {
            "en": [{"url": "https://example.invalid/a.srt", "ext": "srt"}, MANUAL_EN_TRACK]
        },
        "automatic_captions": {"en": [AUTO_EN_TRACK]},
    }

    track = select_caption_track(info)

    assert track is not None
    assert track.kind == "manual"
    assert track.language == "en"
    assert track.ext == "vtt"


def test_select_caption_track_excludes_live_chat_and_empty_language() -> None:
    """Live-chat and empty-language tracks are never selected."""
    info: dict[str, object] = {
        "automatic_captions": {
            "live_chat": [{"url": "https://example.invalid/chat.vtt", "ext": "vtt"}],
            "": [{"url": "https://example.invalid/nolang.vtt", "ext": "vtt"}],
        }
    }

    assert select_caption_track(info) is None


def test_select_caption_track_none_when_entries_unusable() -> None:
    """Tracks whose entries have neither a URL nor inline data are unusable."""
    info: dict[str, object] = {"subtitles": {"en": [{"ext": "vtt", "name": "broken"}]}}

    assert select_caption_track(info) is None


@pytest.mark.parametrize(
    "info",
    [
        {"subtitles": [1]},
        {"subtitles": {"en": [1]}},
        {"automatic_captions": {"en": "not a list"}},
        {"automatic_captions": {"en": [{"url": "ok"}, 1]}},
    ],
)
def test_select_caption_track_ignores_malformed_metadata_shapes(
    info: dict[str, object],
) -> None:
    assert select_caption_track(info) is None


def test_normalize_language_primary_subtag() -> None:
    """Language codes reduce to their ISO 639-1 primary subtag."""
    assert normalize_language("en") == "en"
    assert normalize_language("en-US") == "en"
    assert normalize_language("pt-BR") == "pt"
    assert normalize_language("zh-Hans") == "zh"


def test_vtt_markup_removed_from_segment_text(tmp_path: Path) -> None:
    """Inline tags, timestamp tags, cue settings, and entities are stripped."""
    vtt = """\
WEBVTT

00:00:01.000 --> 00:00:04.000 align:start position:0%
<v Roger Bingham>We are in <c>New York City</c><00:00:02.500><c> again</c>

00:00:04.000 --> 00:00:06.500
Rock &amp; roll &gt; opera &lt; the show
"""
    info: dict[str, object] = {"subtitles": {"en": [{"ext": "vtt", "data": vtt}]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    result = fetcher.fetch("abc123")

    assert result.segments == (
        Segment(start_seconds=1.0, end_seconds=4.0, text="We are in New York City again"),
        Segment(start_seconds=4.0, end_seconds=6.5, text="Rock & roll > opera < the show"),
    )


def test_segments_time_ordered_and_empty_cues_dropped(tmp_path: Path) -> None:
    """Empty cues are dropped; surviving cues keep file order."""
    vtt = """\
WEBVTT

00:00:01.000 --> 00:00:02.000
First

00:00:02.000 --> 00:00:03.000

00:00:03.000 --> 00:00:04.000
<c></c>

00:00:04.000 --> 00:00:05.000
Third
"""
    info: dict[str, object] = {"subtitles": {"en": [{"ext": "vtt", "data": vtt}]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    result = fetcher.fetch("abc123")

    assert result.segments == (
        Segment(start_seconds=1.0, end_seconds=2.0, text="First"),
        Segment(start_seconds=4.0, end_seconds=5.0, text="Third"),
    )


def test_parse_webvtt_handles_header_note_and_timestamp_forms() -> None:
    """Header, NOTE blocks, and MM:SS/HH:MM:SS timestamp forms all parse."""
    vtt = """\
\ufeffWEBVTT - a title

NOTE this comment
spans two lines

00:00:01.500 --> 00:00:03.000
Hello

01:02.500 --> 01:04.000
World
"""
    segments = parse_webvtt(vtt)

    assert segments == (
        Segment(start_seconds=1.5, end_seconds=3.0, text="Hello"),
        Segment(start_seconds=62.5, end_seconds=64.0, text="World"),
    )


def test_transcript_result_records_source_and_language(tmp_path: Path) -> None:
    """Provenance labels survive with an ISO 639-1 primary language code."""
    info: dict[str, object] = {"subtitles": {"en-US": [MANUAL_EN_TRACK]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    result = fetcher.fetch("abc123")

    assert result.source == "manual_captions"
    assert result.language == "en"


def test_live_chat_only_track_raises_captions_unavailable(tmp_path: Path) -> None:
    """A live-chat-only video must signal the ASR fallback, never success."""
    info: dict[str, object] = {
        "automatic_captions": {
            "live_chat": [{"url": "https://example.invalid/chat.vtt", "ext": "vtt"}]
        }
    }
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    with pytest.raises(CaptionsUnavailableError):
        fetcher.fetch("abc123")


def test_no_selected_language_raises_captions_unavailable(tmp_path: Path) -> None:
    """Tracks with an empty language key are unusable and trigger ASR fallback."""
    info: dict[str, object] = {
        "subtitles": {"": [{"url": "https://example.invalid/x.vtt", "ext": "vtt"}]}
    }
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    with pytest.raises(CaptionsUnavailableError):
        fetcher.fetch("abc123")


def test_malformed_vtt_raises_captions_unavailable(tmp_path: Path) -> None:
    """A VTT payload with no parseable cues signals the ASR fallback."""
    info: dict[str, object] = {
        "subtitles": {"en": [{"ext": "vtt", "data": "this is not a vtt\nno cues\n"}]}
    }
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    with pytest.raises(CaptionsUnavailableError):
        fetcher.fetch("abc123")


def test_empty_vtt_raises_captions_unavailable(tmp_path: Path) -> None:
    """An empty VTT payload signals the ASR fallback."""
    info: dict[str, object] = {"subtitles": {"en": [{"ext": "vtt", "data": ""}]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    with pytest.raises(CaptionsUnavailableError):
        fetcher.fetch("abc123")


def test_non_monotonic_segments_raise_captions_unavailable(tmp_path: Path) -> None:
    """Out-of-order cues are malformed captions, not a usable transcript."""
    vtt = """\
WEBVTT

00:00:04.000 --> 00:00:05.000
Late

00:00:01.000 --> 00:00:02.000
Early
"""
    info: dict[str, object] = {"subtitles": {"en": [{"ext": "vtt", "data": vtt}]}}
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    with pytest.raises(CaptionsUnavailableError):
        fetcher.fetch("abc123")


def test_transcript_md_written_atomically_with_header(tmp_path: Path) -> None:
    """fetch() writes transcript.md with provenance header and clean cues."""
    transcript_path = tmp_path / "out" / "transcript.md"
    info: dict[str, object] = {"subtitles": {"en": [MANUAL_EN_TRACK]}}
    fetcher = make_fetcher(info, transcript_path)

    fetcher.fetch("abc123")

    content = transcript_path.read_text(encoding="utf-8")
    assert "# Transcript" in content
    assert "Source: manual_captions" in content
    assert "Language: en" in content
    assert "Video ID: abc123" in content
    assert "[00:00:01.000 --> 00:00:04.000] This is the manual caption text" in content
    assert "WEBVTT" not in content
    assert list(tmp_path.rglob(".transcript-*.tmp")) == []


def test_write_transcript_markdown_replaces_existing_atomically(tmp_path: Path) -> None:
    """Repeated writes replace the target and leave no temp files behind."""
    target = tmp_path / "transcript.md"
    target.write_text("stale", encoding="utf-8")
    result = TranscriptResult(
        segments=(Segment(start_seconds=1.0, end_seconds=2.0, text="Fresh"),),
        source="manual_captions",
        language="en",
    )

    write_transcript_markdown(target, result, "abc123")

    content = target.read_text(encoding="utf-8")
    assert "stale" not in content
    assert "[00:00:01.000 --> 00:00:02.000] Fresh" in content
    assert list(tmp_path.glob(".transcript-*.tmp")) == []


def test_network_failure_raises_transcript_error_not_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real caption-download failure is an error, not an ASR-fallback signal."""
    info: dict[str, object] = {
        "subtitles": {"en": [{"url": "https://example.invalid/captions.vtt", "ext": "vtt"}]}
    }
    fetcher = make_fetcher(info, tmp_path / "transcript.md")

    def fail_open(request: object, timeout: float) -> object:
        raise TimeoutError("connection timed out")

    monkeypatch.setattr("youtube_downloader.captions.urllib.request.urlopen", fail_open)

    with pytest.raises(TranscriptError) as excinfo:
        fetcher.fetch("abc123")
    assert "connection timed out" in str(excinfo.value)
    assert not isinstance(excinfo.value, CaptionsUnavailableError)


def test_ytdl_metadata_failure_raises_transcript_error(tmp_path: Path) -> None:
    """yt-dlp metadata failures become typed TranscriptError with remediation."""
    fetcher = YtDlpCaptionFetcher(
        lambda _opts: FakeYdl({}, error=RuntimeError("network down")),
        tmp_path / "transcript.md",
    )

    with pytest.raises(TranscriptError) as excinfo:
        fetcher.fetch("abc123")
    assert "network down" in str(excinfo.value)
    assert not isinstance(excinfo.value, CaptionsUnavailableError)
