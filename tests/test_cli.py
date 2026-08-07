"""Hermetic tests for the interactive CLI (plan Todo 8).

The CLI's prompting, output, settings, and workflow-construction seams are
injected, so no test contacts YouTube, Ollama, faster-whisper, or ffmpeg: the
workflow is always a recording fake.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from youtube_downloader.cli import main
from youtube_downloader.models import (
    DownloadRequest,
    Format,
    MissingDependencyError,
    SummaryError,
)
from youtube_downloader.settings import Settings

URL = "https://www.youtube.com/watch?v=abc123"
VIDEO_ID = "abc123"
REPORT = (
    "status: complete\n"
    "directory: /out\n"
    "media: /out/v.mp4\n"
    "transcript: /out/transcript.md\n"
    "summary: /out/summary.md\n"
    "metadata: /out/metadata.json"
)


@dataclass
class FakeWorkflow:
    """Records the exact request passed to run() and returns or raises."""

    report: str = REPORT
    error: BaseException | None = None
    requests: list[DownloadRequest] = field(default_factory=list)

    def run(self, request: DownloadRequest) -> str:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.report


def run_cli(
    tmp_path: Path,
    answers: list[str],
    workflow: FakeWorkflow,
    *,
    interrupt: bool = False,
) -> tuple[int, list[str], list[str]]:
    """Drive main() with scripted answers; return (exit code, stdout, stderr)."""
    remaining = list(answers)

    def prompt(_text: str) -> str:
        if interrupt:
            raise KeyboardInterrupt
        return remaining.pop(0)

    out: list[str] = []
    error_out: list[str] = []
    exit_code = main(
        argv=[],
        settings=Settings(output_root=tmp_path),
        prompt=prompt,
        out=out.append,
        error_out=error_out.append,
        workflow_factory=lambda _settings: workflow,
    )
    return exit_code, out, error_out


def test_mp4_session_passes_exact_request(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, _out, _ = run_cli(tmp_path, [URL, "mp4", "key points"], workflow)

    assert exit_code == 0
    assert len(workflow.requests) == 1
    request = workflow.requests[0]
    assert request.url == URL
    assert request.format is Format.MP4
    assert request.summary_goal == "key points"


def test_mp3_session_normalizes_answers(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, _out, _ = run_cli(tmp_path, [URL, " MP3 ", "   "], workflow)

    assert exit_code == 0
    assert len(workflow.requests) == 1
    request = workflow.requests[0]
    assert request.format is Format.MP3
    assert request.summary_goal is None


def test_success_prints_readable_final_report(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, out, _ = run_cli(tmp_path, [URL, "mp4", ""], workflow)

    assert exit_code == 0
    text = "\n".join(out)
    assert "status: complete" in text
    assert "media: /out/v.mp4" in text
    assert "transcript: /out/transcript.md" in text
    assert "summary: /out/summary.md" in text
    assert "metadata: /out/metadata.json" in text


def test_invalid_url_is_rejected_nonzero(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, _out, error_out = run_cli(tmp_path, ["not a url"], workflow)

    assert exit_code != 0
    assert workflow.requests == []
    assert "error:" in error_out[0]
    assert "complete" not in " ".join(error_out)


def test_blank_url_is_rejected_nonzero(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, _out, error_out = run_cli(tmp_path, ["   "], workflow)

    assert exit_code != 0
    assert workflow.requests == []
    assert "error:" in error_out[0]


def test_unknown_format_is_rejected_nonzero(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    exit_code, _out, error_out = run_cli(tmp_path, [URL, "wav"], workflow)

    assert exit_code != 0
    assert workflow.requests == []
    assert "mp3" in error_out[0]
    assert "complete" not in " ".join(error_out)


def test_preflight_failure_returns_nonzero_without_complete(tmp_path: Path) -> None:
    workflow = FakeWorkflow(error=MissingDependencyError("ffmpeg is not installed"))

    exit_code, out, error_out = run_cli(tmp_path, [URL, "mp4", ""], workflow)

    assert exit_code != 0
    assert len(workflow.requests) == 1
    assert "ffmpeg is not installed" in error_out[0]
    assert "complete" not in " ".join(out + error_out)


def test_partial_failure_reports_exact_retained_paths(tmp_path: Path) -> None:
    workflow = FakeWorkflow(error=SummaryError("ollama down"))
    directory = tmp_path / f"Some Title [{VIDEO_ID}]"
    directory.mkdir()
    media = directory / f"Some Title [{VIDEO_ID}].mp4"
    media.write_bytes(b"media")
    transcript = directory / "transcript.md"
    transcript.write_text("# Transcript\n")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "status": "partial",
                "completed_stage": "transcript",
                "retained_paths": [str(media), str(transcript)],
            }
        )
    )

    exit_code, out, error_out = run_cli(tmp_path, [URL, "mp4", ""], workflow)

    assert exit_code != 0
    assert len(workflow.requests) == 1
    text = " ".join(error_out)
    assert "ollama down" in error_out[0]
    assert "partial" in text
    assert str(media) in text
    assert str(transcript) in text
    assert "complete" not in " ".join(out + error_out)


def test_keyboard_interrupt_at_prompt_exits_nonzero_without_complete(
    tmp_path: Path,
) -> None:
    workflow = FakeWorkflow()

    exit_code, out, error_out = run_cli(tmp_path, [], workflow, interrupt=True)

    assert exit_code != 0
    assert workflow.requests == []
    assert error_out
    assert "complete" not in " ".join(out + error_out)


def test_keyboard_interrupt_during_run_exits_nonzero_without_complete(
    tmp_path: Path,
) -> None:
    workflow = FakeWorkflow(error=KeyboardInterrupt())

    exit_code, out, error_out = run_cli(tmp_path, [URL, "mp4", ""], workflow)

    assert exit_code != 0
    assert len(workflow.requests) == 1
    assert error_out
    assert "complete" not in " ".join(out + error_out)


def test_end_of_input_exits_nonzero_without_complete(tmp_path: Path) -> None:
    workflow = FakeWorkflow()

    def prompt(_text: str) -> str:
        raise EOFError

    out: list[str] = []
    error_out: list[str] = []
    exit_code = main(
        argv=[],
        settings=Settings(output_root=tmp_path),
        prompt=prompt,
        out=out.append,
        error_out=error_out.append,
        workflow_factory=lambda _settings: workflow,
    )

    assert exit_code != 0
    assert workflow.requests == []
    assert error_out
    assert "complete" not in " ".join(out + error_out)


def test_importing_the_cli_never_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_prompt(_text: str) -> str:
        raise AssertionError("the CLI must not prompt during import")

    monkeypatch.setattr("builtins.input", fail_prompt)
    module = importlib.import_module("youtube_downloader.cli")
    importlib.reload(module)
    assert callable(module.main)
