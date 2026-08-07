"""Tests for typed domain models, errors, settings, and protocols (plan Todo 2)."""

import dataclasses
from pathlib import Path

import pytest

from youtube_downloader.models import (
    AsrError,
    AsrTranscriber,
    CaptionFetcher,
    CaptionsUnavailableError,
    DownloaderError,
    DownloadRequest,
    Format,
    InvalidFormatError,
    InvalidURLError,
    MediaDownloader,
    MediaDownloadError,
    MediaResult,
    MissingDependencyError,
    Segment,
    SettingsError,
    Summarizer,
    SummaryError,
    SummaryResult,
    TranscriptError,
    TranscriptResult,
    validate_url,
)
from youtube_downloader.settings import Settings

VALID_HTTP_URLS = (
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtu.be/dQw4w9WgXcQ",
    "https://example.com/video?id=1",
)

INVALID_URLS = (
    "",
    "   ",
    "not a url",
    "youtube.com/watch?v=dQw4w9WgXcQ",
    "ftp://youtube.com/video",
    "file:///etc/passwd",
    "https://",
    "http://user:pass@youtube.com/watch?v=dQw4w9WgXcQ",
)


@pytest.mark.parametrize("url", VALID_HTTP_URLS)
def test_download_request_accepts_http_urls(url: str) -> None:
    assert DownloadRequest(url, Format.MP4).url == url


@pytest.mark.parametrize("url", INVALID_URLS)
def test_download_request_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidURLError):
        DownloadRequest(url, Format.MP4)


def test_validate_url_strips_surrounding_whitespace() -> None:
    assert validate_url("  https://youtu.be/dQw4w9WgXcQ  ") == "https://youtu.be/dQw4w9WgXcQ"


def test_download_request_summary_goal_defaults_to_none() -> None:
    request = DownloadRequest("https://youtu.be/dQw4w9WgXcQ", Format.MP4)
    assert request.summary_goal is None
    goal = DownloadRequest(
        "https://youtu.be/dQw4w9WgXcQ", Format.MP4, "summarize the key points"
    )
    assert goal.summary_goal == "summarize the key points"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mp3", Format.MP3),
        ("MP3", Format.MP3),
        (" Mp4 ", Format.MP4),
    ],
)
def test_format_from_str(raw: str, expected: Format) -> None:
    assert Format.from_str(raw) is expected


@pytest.mark.parametrize("raw", ["", "webm", "mp3x", "wav", "M4A"])
def test_format_from_str_rejects_unknown(raw: str) -> None:
    with pytest.raises(InvalidFormatError):
        Format.from_str(raw)


ERROR_CLASSES = (
    InvalidURLError,
    InvalidFormatError,
    SettingsError,
    MediaDownloadError,
    TranscriptError,
    CaptionsUnavailableError,
    AsrError,
    SummaryError,
    MissingDependencyError,
)


@pytest.mark.parametrize("cls", ERROR_CLASSES)
def test_errors_are_downloader_errors(cls: type[DownloaderError]) -> None:
    assert issubclass(cls, DownloaderError)


@pytest.mark.parametrize("cls", ERROR_CLASSES)
def test_errors_carry_actionable_messages(cls: type[DownloaderError]) -> None:
    assert str(cls()), f"{cls.__name__} has an empty default message"
    assert str(cls("custom remediation")) == "custom remediation"


def test_captions_unavailable_is_a_transcript_error() -> None:
    assert issubclass(CaptionsUnavailableError, TranscriptError)


def test_media_result_carries_optional_metadata() -> None:
    result = MediaResult(Path("/tmp/video.mp4"), "dQw4w9WgXcQ", "Never Gonna Give You Up")
    assert result.final_path == Path("/tmp/video.mp4")
    assert result.video_id == "dQw4w9WgXcQ"
    assert result.title == "Never Gonna Give You Up"
    assert result.uploader is None
    assert result.duration_seconds is None

    full = MediaResult(
        Path("/tmp/video.mp4"),
        "dQw4w9WgXcQ",
        "Never Gonna Give You Up",
        uploader="Rick Astley",
        duration_seconds=213.0,
        view_count=1_000_000,
    )
    assert full.uploader == "Rick Astley"
    assert full.duration_seconds == 213.0
    assert full.view_count == 1_000_000


def test_transcript_result_holds_normalized_segments() -> None:
    segments = (Segment(0.0, 2.5, "Hello"), Segment(2.5, 5.0, "world"))
    result = TranscriptResult(segments, "manual_captions", "en")
    assert result.segments == segments
    assert result.source == "manual_captions"
    assert result.language == "en"


def test_summary_result_carries_model_and_source() -> None:
    path = Path("/tmp/videos/T [abc123]/transcript.md")
    result = SummaryResult("The video is about X.", "llama3.2", path)
    assert result.text == "The video is about X."
    assert result.model == "llama3.2"
    assert result.source_transcript_path == path
    assert SummaryResult("text", "llama3.2").source_transcript_path is None


def test_models_are_frozen() -> None:
    request = DownloadRequest("https://example.com/v", Format.MP3)
    replaced = dataclasses.replace(request, url="https://example.com/w")
    assert request.url == "https://example.com/v"
    assert replaced.url == "https://example.com/w"


class FakeDownloader:
    def download(self, request: DownloadRequest, destination: Path) -> MediaResult:
        return MediaResult(destination, "abc123", "Title")


class NotADownloader:
    pass


def test_media_downloader_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeDownloader(), MediaDownloader)
    assert not isinstance(NotADownloader(), MediaDownloader)


def test_service_protocols_are_runtime_checkable() -> None:
    for protocol in (CaptionFetcher, AsrTranscriber, Summarizer):
        assert isinstance(NotADownloader(), protocol) is False


VALID_LOOPBACK_ENDPOINTS = (
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "http://[::1]:11434",
    "https://127.0.0.1:11434",
    "http://127.0.0.1:11434/",
    "http://127.0.0.2:11434",
)


@pytest.mark.parametrize("endpoint", VALID_LOOPBACK_ENDPOINTS)
def test_settings_accepts_loopback_endpoints(endpoint: str) -> None:
    assert Settings(ollama_endpoint=endpoint).ollama_endpoint == endpoint


INVALID_ENDPOINTS = (
    "http://ollama.example.com:11434",
    "http://8.8.8.8:11434",
    "http://user:pass@127.0.0.1:11434",
    "https://user@localhost:11434",
    "ftp://127.0.0.1:11434",
    "file:///etc/hosts",
    "127.0.0.1:11434",
    "not a url",
    "http://",
    "  http://localhost:11434",
)


@pytest.mark.parametrize("endpoint", INVALID_ENDPOINTS)
def test_settings_rejects_non_loopback_or_malformed_endpoints(endpoint: str) -> None:
    with pytest.raises(SettingsError):
        Settings(ollama_endpoint=endpoint)


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.output_root == Path("downloads")
    assert settings.whisper_model == "small"
    assert settings.whisper_device == "cpu"
    assert settings.whisper_compute_type == "int8"
    assert settings.ollama_endpoint == "http://127.0.0.1:11434"
    assert settings.ollama_model == "llama3.2"


@pytest.mark.parametrize(
    "field",
    ["whisper_model", "whisper_device", "whisper_compute_type", "ollama_model"],
)
def test_settings_rejects_blank_model_fields(field: str) -> None:
    with pytest.raises(SettingsError):
        Settings(**{field: "   "})
