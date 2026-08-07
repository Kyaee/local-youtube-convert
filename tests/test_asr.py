"""Tests for the local faster-whisper transcription fallback (plan Todo 5).

Every test fakes the Whisper model through the injectable ``model_factory``
and import-probe seams -- no real faster-whisper inference, no network, no
model downloads, no API keys, no cloud.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from youtube_downloader.asr import (
    WhisperAsrTranscriber,
    WhisperModelLike,
    diagnose,
)
from youtube_downloader.models import (
    AsrError,
    AsrTranscriber,
    MissingDependencyError,
    Segment,
    TranscriptResult,
)


@dataclass(frozen=True)
class _FakeSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class _FakeInfo:
    language: str
    language_probability: float = 0.9


def _yield_segments(segments: list[_FakeSegment]) -> Iterator[_FakeSegment]:
    yield from segments


class _FakeWhisperModel:
    """Scripted fake for the ``WhisperModelLike`` protocol; records calls.

    ``load_error`` is raised by the model factory (simulating a model-load or
    one-time-download failure); ``transcribe_error`` is raised by
    ``transcribe`` (simulating a decoding failure).
    """

    def __init__(
        self,
        segments: list[_FakeSegment] | None = None,
        *,
        language: str = "en",
        load_error: Exception | None = None,
        transcribe_error: Exception | None = None,
    ) -> None:
        self._segments = list(segments or [])
        self._language = language
        self._load_error = load_error
        self._transcribe_error = transcribe_error
        self.calls: list[tuple[Path, str | None]] = []

    def transcribe(self, media: Path, language: str | None = None):
        self.calls.append((media, language))
        if self._transcribe_error is not None:
            raise self._transcribe_error
        return _yield_segments(self._segments), _FakeInfo(self._language)


def _build_transcriber(
    fake: _FakeWhisperModel, model_name: str = "small", **kwargs: object
) -> WhisperAsrTranscriber:
    def factory(model: str, device: str, compute_type: str) -> WhisperModelLike:
        if fake._load_error is not None:
            raise fake._load_error
        return fake

    return WhisperAsrTranscriber(
        model_name,
        device="cpu",
        compute_type="int8",
        model_factory=factory,
        **kwargs,
    )


def _media(tmp_path: Path, *, size: int = 1) -> Path:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"\x00" * size)
    return path


# --- startup diagnostics ---------------------------------------------------


def test_diagnose_raises_missing_dependency_when_import_fails() -> None:
    def failing_probe() -> object:
        raise ModuleNotFoundError("No module named 'faster_whisper'")

    with pytest.raises(MissingDependencyError) as exc:
        diagnose(failing_probe)

    message = str(exc.value)
    assert "pip install faster-whisper" in message
    assert "uv add faster-whisper" in message


def test_diagnose_passes_when_import_succeeds() -> None:
    def passing_probe() -> object:
        return object()

    assert diagnose(passing_probe) is None


# --- successful transcription ---------------------------------------------


def test_transcribe_materializes_segment_generator_into_tuple(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 4.0, "hello world")])

    result = _build_transcriber(fake).transcribe(_media(tmp_path))

    assert isinstance(result.segments, tuple)
    assert result.segments == (Segment(0.0, 4.0, "hello world"),)


def test_successful_transcription_preserves_segment_order(tmp_path: Path) -> None:
    fake = _FakeWhisperModel(
        [
            _FakeSegment(0.0, 2.0, "one"),
            _FakeSegment(2.0, 5.0, "two"),
            _FakeSegment(5.0, 9.0, "three"),
        ]
    )

    result = _build_transcriber(fake).transcribe(_media(tmp_path))

    assert isinstance(result, TranscriptResult)
    assert [segment.text for segment in result.segments] == ["one", "two", "three"]
    assert result.segments[1].start_seconds == 2.0
    assert all(
        isinstance(segment.start_seconds, float)
        and isinstance(segment.end_seconds, float)
        for segment in result.segments
    )


def test_whitespace_only_segments_are_dropped(tmp_path: Path) -> None:
    fake = _FakeWhisperModel(
        [_FakeSegment(0.0, 1.0, "  hi  there "), _FakeSegment(1.0, 2.0, "   ")]
    )

    result = _build_transcriber(fake).transcribe(_media(tmp_path))

    assert result.segments == (Segment(0.0, 1.0, "hi there"),)


# --- provenance: source and language ---------------------------------------


def test_source_is_asr_model(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")])

    result = _build_transcriber(fake, model_name="large-v3").transcribe(_media(tmp_path))

    assert result.source == "asr:large-v3"


def test_detected_language_flows_into_result_as_primary_subtag(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "ola")], language="pt-BR")

    result = _build_transcriber(fake).transcribe(_media(tmp_path))

    assert result.language == "pt"


def test_language_detection_is_automatic(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")])
    media = _media(tmp_path)

    _build_transcriber(fake).transcribe(media)

    assert fake.calls == [(media, None)]


def test_blank_detected_language_raises_asr_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")], language="")

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    assert "no language" in str(exc.value)


# --- model load and one-time download failures ------------------------------


def test_model_load_failure_raises_asr_error_with_one_time_download_remediation(
    tmp_path: Path,
) -> None:
    fake = _FakeWhisperModel(load_error=OSError("model not cached: network blocked"))

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    message = str(exc.value)
    assert "Hugging Face" in message
    assert "one-time" in message
    assert "preserved" in message


def test_missing_package_at_load_raises_missing_dependency_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel(load_error=ModuleNotFoundError("No module named 'faster_whisper'"))

    with pytest.raises(MissingDependencyError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    message = str(exc.value)
    assert "pip install faster-whisper" in message
    assert "uv add faster-whisper" in message


def test_transcribe_failure_raises_asr_error_with_remediation(tmp_path: Path) -> None:
    fake = _FakeWhisperModel(transcribe_error=RuntimeError("decode failed"))

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    assert "preserved" in str(exc.value)


# --- media validation ------------------------------------------------------


def test_empty_media_file_raises_asr_error_without_touching_model(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")])

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path, size=0))

    assert "empty" in str(exc.value)
    assert fake.calls == []


def test_missing_media_file_raises_asr_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")])

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(tmp_path / "absent.mp3")

    assert "does not exist" in str(exc.value)
    assert fake.calls == []


# --- model output validation ------------------------------------------------


def test_empty_segments_raise_asr_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([])

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    assert "produced no segments" in str(exc.value)


def test_non_monotonic_start_timestamps_raise_asr_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel(
        [_FakeSegment(2.0, 4.0, "first"), _FakeSegment(0.5, 3.0, "second")]
    )

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    assert "non-monotonic" in str(exc.value)


def test_segment_with_end_before_start_raises_asr_error(tmp_path: Path) -> None:
    fake = _FakeWhisperModel([_FakeSegment(5.0, 1.0, "backwards")])

    with pytest.raises(AsrError) as exc:
        _build_transcriber(fake).transcribe(_media(tmp_path))

    assert "non-monotonic" in str(exc.value)


# --- construction defaults and protocol conformance --------------------------


def test_constructor_defaults_to_small_cpu_int8(tmp_path: Path) -> None:
    captured: list[tuple[str, str, str]] = []

    def recording_factory(model: str, device: str, compute_type: str) -> WhisperModelLike:
        captured.append((model, device, compute_type))
        return _FakeWhisperModel([_FakeSegment(0.0, 1.0, "hi")])

    WhisperAsrTranscriber(model_factory=recording_factory).transcribe(_media(tmp_path))

    assert captured == [("small", "cpu", "int8")]


@pytest.mark.parametrize(
    "kwargs",
    [{"model": "  "}, {"device": ""}, {"compute_type": ""}],
)
def test_constructor_rejects_blank_settings(kwargs: dict[str, str]) -> None:
    with pytest.raises(AsrError):
        WhisperAsrTranscriber(**kwargs)


def test_whisper_asr_transcriber_satisfies_asr_transcriber_protocol() -> None:
    transcriber = WhisperAsrTranscriber()
    assert isinstance(transcriber, AsrTranscriber)
