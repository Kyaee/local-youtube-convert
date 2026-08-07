from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeAlias, TypedDict, final

from youtube_downloader.asr import diagnose
from youtube_downloader.captions import write_transcript_markdown
from youtube_downloader.models import (
    AsrError,
    AsrTranscriber,
    CaptionFetcher,
    CaptionsUnavailableError,
    DownloadRequest,
    MediaDownloader,
    Summarizer,
    SummaryError,
    SummaryResult,
    TranscriptError,
    TranscriptResult,
)
from youtube_downloader.paths import ArtifactPaths, artifact_paths
from youtube_downloader.settings import Settings

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class WorkflowMedia(MediaDownloader, Protocol):
    def extract_metadata(self, url: str) -> dict[str, object]: ...


class Metadata(TypedDict):
    video_id: str
    title: str
    url: str
    request: dict[str, JsonValue]
    artifact_paths: dict[str, JsonValue]
    transcript: dict[str, JsonValue]
    models: dict[str, JsonValue]
    status: str
    completed_stage: str
    retained_paths: list[JsonValue]
    errors: list[JsonValue]


def _write_atomic_json(path: Path, payload: Metadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".metadata-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            _ = handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _artifact_values(paths: ArtifactPaths) -> dict[str, JsonValue]:
    return {name: str(value) for name, value in {
        "directory": paths.directory,
        "media": paths.media,
        "transcript": paths.transcript,
        "summary": paths.summary,
        "metadata": paths.metadata,
    }.items()}


def _summary_markdown(result: SummaryResult) -> str:
    return f"# Summary\n\n- model: {result.model}\n\n{result.text.rstrip()}\n"


def _write_summary(path: Path, result: SummaryResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".summary-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(_summary_markdown(result))
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _retained(paths: ArtifactPaths) -> list[JsonValue]:
    return [
        str(path)
        for path in (paths.media, paths.transcript, paths.summary, paths.metadata)
        if path.is_file() or path is paths.metadata
    ]


@final
class DownloadWorkflow:
    _media: WorkflowMedia
    _captions: CaptionFetcher
    _asr: AsrTranscriber
    _summarizer: Summarizer
    _settings: Settings
    _asr_diagnose: Callable[[], None]

    def __init__(
        self,
        media: WorkflowMedia,
        captions: CaptionFetcher,
        asr: AsrTranscriber,
        summarizer: Summarizer,
        settings: Settings,
        *,
        asr_diagnose: Callable[[], None] = diagnose,
    ) -> None:
        self._media = media
        self._captions = captions
        self._asr = asr
        self._summarizer = summarizer
        self._settings = settings
        self._asr_diagnose = asr_diagnose

    def run(self, request: DownloadRequest) -> str:
        self._asr_diagnose()
        raw_metadata = self._media.extract_metadata(request.url)
        video_id = raw_metadata.get("id")
        title = raw_metadata.get("title")
        if not isinstance(video_id, str) or not video_id:
            raise RuntimeError("Metadata did not contain a video id")
        if not isinstance(title, str) or not title:
            title = video_id
        paths = artifact_paths(self._settings.output_root, title, video_id, request.format)
        metadata = self._metadata(request, video_id, title, paths)
        media_result = self._media.download(request, paths.media)
        metadata["completed_stage"] = "media"
        try:
            transcript = self._transcript(video_id, media_result.final_path)
            metadata["transcript"] = {"source": transcript.source, "language": transcript.language}
            write_transcript_markdown(paths.transcript, transcript, video_id)
            metadata["completed_stage"] = "transcript"
            summary = self._summarizer.summarize(transcript, request.summary_goal)
            _write_summary(paths.summary, summary)
            metadata["models"] = {
                "asr": transcript.source.removeprefix("asr:"),
                "summary": summary.model,
            }
            metadata["completed_stage"] = "summary"
        except (AsrError, TranscriptError, SummaryError, OSError) as exc:
            metadata["status"] = "partial"
            metadata["errors"] = [str(exc)]
            metadata["retained_paths"] = _retained(paths)
            _write_atomic_json(paths.metadata, metadata)
            raise
        metadata["status"] = "complete"
        metadata["completed_stage"] = "complete"
        metadata["retained_paths"] = _retained(paths)
        _write_atomic_json(paths.metadata, metadata)
        return self._report(metadata)

    def _metadata(
        self, request: DownloadRequest, video_id: str, title: str, paths: ArtifactPaths
    ) -> Metadata:
        return {
            "video_id": video_id,
            "title": title,
            "url": request.url,
            "request": {"format": request.format.value, "summary_goal": request.summary_goal},
            "artifact_paths": _artifact_values(paths),
            "transcript": {"source": "pending", "language": "pending"},
            "models": {"asr": self._settings.whisper_model, "summary": self._settings.ollama_model},
            "status": "running",
            "completed_stage": "metadata",
            "retained_paths": [],
            "errors": [],
        }

    def _transcript(self, video_id: str, media_path: Path) -> TranscriptResult:
        try:
            return self._captions.fetch(video_id)
        except CaptionsUnavailableError:
            return self._asr.transcribe(media_path)

    def _report(self, metadata: Metadata) -> str:
        artifact_lines = "\n".join(
            f"{name}: {value}" for name, value in metadata["artifact_paths"].items()
        )
        return f"status: {metadata['status']}\n{artifact_lines}"
