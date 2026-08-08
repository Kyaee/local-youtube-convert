from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from youtube_downloader.models import (
    CaptionsUnavailableError,
    DownloadRequest,
    Format,
    MediaDownloadError,
    MediaResult,
    Segment,
    SummaryError,
    SummaryResult,
    TranscriptError,
    TranscriptResult,
)
from youtube_downloader.settings import Settings
from youtube_downloader.workflow import DownloadWorkflow

URL = "https://www.youtube.com/watch?v=abc123"
VIDEO_ID = "abc123"
TITLE = "A Test Video"


@dataclass
class FakeMedia:
    root: Path
    fail: Exception | None = None
    calls: list[Path] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def extract_metadata(self, url: str) -> dict[str, object]:
        return {"id": VIDEO_ID, "title": TITLE, "uploader": "Tester"}

    def download(self, request: DownloadRequest, destination: Path) -> MediaResult:
        assert self.calls is not None
        self.calls.append(destination)
        if self.fail is not None:
            raise self.fail
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"media")
        return MediaResult(destination, VIDEO_ID, TITLE, uploader="Tester")


@dataclass
class MissingMedia:
    def extract_metadata(self, url: str) -> dict[str, object]:
        return {"id": VIDEO_ID, "title": TITLE}

    def download(self, request: DownloadRequest, destination: Path) -> MediaResult:
        return MediaResult(destination, VIDEO_ID, TITLE)


@dataclass
class FakeCaptions:
    result: TranscriptResult | None = None
    error: Exception | None = None
    calls: list[str] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def fetch(self, video_id: str) -> TranscriptResult:
        assert self.calls is not None
        self.calls.append(video_id)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakeAsr:
    result: TranscriptResult
    diagnosed: bool = False
    calls: list[Path] | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def diagnose(self) -> None:
        self.diagnosed = True

    def transcribe(self, media_path: Path) -> TranscriptResult:
        assert self.calls is not None
        self.calls.append(media_path)
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class FakeSummary:
    result: SummaryResult = SummaryResult("A summary.", "fake-summary")
    error: Exception | None = None
    calls: list[TranscriptResult] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    def summarize(self, transcript: TranscriptResult, summary_goal: str | None) -> SummaryResult:
        assert self.calls is not None
        self.calls.append(transcript)
        if self.error is not None:
            raise self.error
        return self.result


def transcript(source: str = "manual_captions") -> TranscriptResult:
    return TranscriptResult((Segment(0.0, 1.0, "hello"),), source, "en")


def make_workflow(
    tmp_path: Path,
    fmt: Format = Format.MP4,
    *,
    captions: FakeCaptions | None = None,
    asr: FakeAsr | None = None,
    summary: FakeSummary | None = None,
    media: FakeMedia | None = None,
) -> DownloadWorkflow:
    return DownloadWorkflow(
        media=media or FakeMedia(tmp_path),
        captions=captions or FakeCaptions(transcript()),
        asr=asr or FakeAsr(transcript("asr:small")),
        summarizer=summary or FakeSummary(),
        settings=Settings(output_root=tmp_path),
        asr_diagnose=(asr.diagnose if asr is not None else lambda: None),
    )


def test_manual_captions_complete_and_metadata_is_atomic(tmp_path: Path) -> None:
    workflow = make_workflow(tmp_path)

    report = workflow.run(DownloadRequest(URL, Format.MP4, "key points"))

    directory = tmp_path / "A Test Video [abc123]"
    metadata = json.loads((directory / "metadata.json").read_text())
    assert (directory / "A Test Video [abc123].mp4").is_file()
    assert (directory / "transcript.md").is_file()
    assert (directory / "summary.md").is_file()
    assert metadata["status"] == "complete"
    assert metadata["completed_stage"] == "complete"
    assert metadata["transcript"]["source"] == "manual_captions"
    assert "summary.md" in report
    assert list(directory.glob("*.tmp")) == []


def test_automatic_captions_and_mp3_are_supported(tmp_path: Path) -> None:
    workflow = make_workflow(
        tmp_path, Format.MP3, captions=FakeCaptions(transcript("auto_captions"))
    )

    workflow.run(DownloadRequest(URL, Format.MP3))

    directory = tmp_path / "A Test Video [abc123]"
    assert (directory / "A Test Video [abc123].mp3").is_file()
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["request"]["format"] == "mp3"
    assert metadata["transcript"]["source"] == "auto_captions"


def test_asr_fallback_runs_only_for_unavailable_captions(tmp_path: Path) -> None:
    asr = FakeAsr(transcript("asr:small"))
    captions = FakeCaptions(error=CaptionsUnavailableError())
    workflow = make_workflow(tmp_path, captions=captions, asr=asr)

    workflow.run(DownloadRequest(URL, Format.MP4))

    assert asr.diagnosed is True
    assert len(asr.calls or []) == 1
    metadata = json.loads((tmp_path / "A Test Video [abc123]" / "metadata.json").read_text())
    assert metadata["transcript"]["source"] == "asr:small"


def test_caption_failure_preserves_media_and_writes_partial_metadata(tmp_path: Path) -> None:
    workflow = make_workflow(tmp_path, captions=FakeCaptions(error=TranscriptError("broken")))

    with pytest.raises(TranscriptError, match="broken"):
        workflow.run(DownloadRequest(URL, Format.MP4))

    directory = tmp_path / "A Test Video [abc123]"
    metadata = json.loads((directory / "metadata.json").read_text())
    assert (directory / "A Test Video [abc123].mp4").is_file()
    assert metadata["status"] == "partial"
    assert metadata["completed_stage"] == "media"
    assert "broken" in metadata["errors"][0]
    assert metadata["retained_paths"]


def test_summary_failure_never_claims_complete(tmp_path: Path) -> None:
    workflow = make_workflow(tmp_path, summary=FakeSummary(error=SummaryError("ollama down")))

    with pytest.raises(SummaryError, match="ollama down"):
        workflow.run(DownloadRequest(URL, Format.MP4))

    metadata = json.loads((tmp_path / "A Test Video [abc123]" / "metadata.json").read_text())
    assert metadata["status"] == "partial"
    assert metadata["completed_stage"] == "transcript"
    assert (tmp_path / "A Test Video [abc123]" / "transcript.md").is_file()
    assert not (tmp_path / "A Test Video [abc123]" / "summary.md").exists()


def test_media_failure_propagates_without_metadata_or_false_completion(tmp_path: Path) -> None:
    workflow = make_workflow(tmp_path, media=FakeMedia(tmp_path, fail=RuntimeError("media down")))

    with pytest.raises(RuntimeError, match="media down"):
        workflow.run(DownloadRequest(URL, Format.MP4))

    directory = tmp_path / "A Test Video [abc123]"
    assert not (directory / "metadata.json").exists()
    assert not directory.exists()


def test_missing_media_artifact_never_claims_complete(tmp_path: Path) -> None:
    workflow = make_workflow(tmp_path, media=MissingMedia())

    with pytest.raises(MediaDownloadError, match="media artifact"):
        workflow.run(DownloadRequest(URL, Format.MP4))

    directory = tmp_path / "A Test Video [abc123]"
    assert not (directory / "metadata.json").exists()
    assert not (directory / "transcript.md").exists()
    assert not (directory / "summary.md").exists()
