"""Local speech-to-text transcription fallback backed by faster-whisper.

Implements the :class:`AsrTranscriber` protocol from
:mod:`youtube_downloader.models`: given a local media file it runs Whisper
on-device (no API keys, no cloud) and returns the same normalized
:class:`TranscriptResult` contract the caption fetcher produces, with
``source="asr:<model>"`` and the model-detected ISO 639-1 language.

Orchestration split (load-bearing for the workflow): ASR runs only after
caption resolution raises :class:`CaptionsUnavailableError`; :meth:`transcribe`
is a pure function of the media path so the workflow can skip it freely, and
the startup :func:`diagnose` import check runs before any download begins.
``faster_whisper`` is imported lazily at the model boundary. Every failure
path raises a typed error -- :class:`MissingDependencyError` for a missing
package, :class:`AsrError` with one-time-download remediation for model and
transcription failures -- never a silent swallow.
"""

from __future__ import annotations

import importlib
import itertools
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol, cast, final

from youtube_downloader.captions import normalize_language
from youtube_downloader.models import (
    AsrError,
    MissingDependencyError,
    Segment,
    TranscriptResult,
)

_DEFAULT_MODEL = "small"
_DEFAULT_DEVICE = "cpu"
_DEFAULT_COMPUTE_TYPE = "int8"

_IMPORT_REMEDIATION = (
    "faster-whisper is not installed or is not importable under this Python. "
    "Install it with `pip install faster-whisper` or `uv add faster-whisper`, "
    "then retry."
)


def _model_load_remediation(model: str) -> str:
    return (
        f"Failed to load the Whisper model {model!r}. faster-whisper downloads "
        "the model from Hugging Face on first use (a one-time local download); "
        "it may not be cached yet, the download may be blocked, or the cache "
        "may be corrupt. The media file is preserved and no data is lost -- "
        "retry after the model is cached."
    )


def _transcribe_remediation(model: str, media_path: Path) -> str:
    return (
        f"Local transcription of {media_path} with model {model!r} failed. "
        "The media file is preserved and no data is lost -- retry the download "
        "to attempt transcription again."
    )


def _empty_remediation(media_path: Path) -> str:
    return (
        f"Local transcription of {media_path} produced no segments; the audio "
        "may be silent. The media file is preserved and no data is lost."
    )


def _monotonic_remediation(media_path: Path) -> str:
    return (
        f"Local transcription of {media_path} produced non-monotonic "
        "timestamps, so the transcript is unusable. The media file is "
        "preserved and no data is lost."
    )


class WhisperSegment(Protocol):
    """One raw faster-whisper segment: start/end seconds plus text."""

    start: float
    end: float
    text: str


class WhisperTranscription(Protocol):
    """The segment iterable faster-whisper yields from ``transcribe``."""

    def __iter__(self) -> Iterator[WhisperSegment]: ...


class WhisperInfo(Protocol):
    """Detected-language metadata paired with the segment iterable."""

    language: str
    language_probability: float


class WhisperModelLike(Protocol):
    """Minimal faster-whisper ``WhisperModel`` surface the transcriber needs."""

    def transcribe(
        self, media: Path, language: str | None = None
    ) -> tuple[WhisperTranscription, WhisperInfo]: ...


def _import_faster_whisper() -> object:
    """Import the ``faster_whisper`` package (lazy, at the boundary only)."""
    return importlib.import_module("faster_whisper")


def diagnose(import_probe: Callable[[], object] | None = None) -> None:
    """Verify ``faster_whisper`` imports before any download begins.

    Raises :class:`MissingDependencyError` with ``pip``/``uv`` remediation when
    the package is not importable under the supported Python. The workflow
    calls this during startup preflight; the default probe imports the real
    package, tests inject a fake.
    """
    probe = import_probe if import_probe is not None else _import_faster_whisper
    try:
        _ = probe()
    except ImportError as exc:
        raise MissingDependencyError(_IMPORT_REMEDIATION) from exc


def _default_model_factory(model: str, device: str, compute_type: str) -> WhisperModelLike:
    """Build a real faster-whisper model; the import happens here, lazily."""
    module = importlib.import_module("faster_whisper")
    constructor = cast(
        Callable[..., object],
        cast(object, getattr(module, "WhisperModel")),  # noqa: B009 - untyped boundary
    )
    instance = constructor(model, device=device, compute_type=compute_type)
    return cast(WhisperModelLike, instance)


@final
class WhisperAsrTranscriber:
    """Transcribes local media with faster-whisper into normalized segments.

    Implements :class:`AsrTranscriber`. ``model`` is a faster-whisper model
    name or path; ``device``/``compute_type`` default to CPU INT8 (no GPU
    required). ``model_factory`` is an injectable seam: tests substitute a
    fake so no model download or inference ever happens.
    """

    _model: str
    _device: str
    _compute_type: str
    _factory: Callable[[str, str, str], WhisperModelLike]

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        *,
        device: str = _DEFAULT_DEVICE,
        compute_type: str = _DEFAULT_COMPUTE_TYPE,
        model_factory: Callable[[str, str, str], WhisperModelLike] | None = None,
    ) -> None:
        if not model.strip():
            raise AsrError("The Whisper model name must not be blank")
        if not device.strip():
            raise AsrError("The Whisper device must not be blank")
        if not compute_type.strip():
            raise AsrError("The Whisper compute type must not be blank")
        self._model = model
        self._device = device
        self._compute_type = compute_type
        self._factory = model_factory if model_factory is not None else _default_model_factory

    def transcribe(self, media_path: Path) -> TranscriptResult:
        """Transcribe *media_path* into materialized, timestamped segments.

        Raises :class:`AsrError` with remediation when the media is missing or
        empty, the model cannot be loaded, or the model output is empty or
        non-monotonic; raises :class:`MissingDependencyError` when the package
        is not installed.
        """
        self._validate_media(media_path)
        model = self._load_model()
        try:
            segments, info = model.transcribe(media_path, language=None)
        except Exception as exc:
            raise AsrError(_transcribe_remediation(self._model, media_path)) from exc
        normalized = self._materialize(segments, media_path)
        language = normalize_language(info.language)
        if not language.strip():
            raise AsrError(
                f"Local transcription of {media_path} reported no language; "
                + "the transcript is unusable"
            )
        return TranscriptResult(
            segments=normalized,
            source=f"asr:{self._model}",
            language=language,
        )

    def _validate_media(self, media_path: Path) -> None:
        if not media_path.is_file():
            raise AsrError(f"Media file {media_path} does not exist; nothing to transcribe")
        try:
            size = media_path.stat().st_size
        except OSError as exc:
            raise AsrError(f"Media file {media_path} is not readable: {exc}") from exc
        if size == 0:
            raise AsrError(f"Media file {media_path} is empty; there is no audio to transcribe")

    def _load_model(self) -> WhisperModelLike:
        try:
            return self._factory(self._model, self._device, self._compute_type)
        except ImportError as exc:
            raise MissingDependencyError(_IMPORT_REMEDIATION) from exc
        except Exception as exc:
            raise AsrError(_model_load_remediation(self._model)) from exc

    def _materialize(
        self, transcription: WhisperTranscription, media_path: Path
    ) -> tuple[Segment, ...]:
        """Fully consume the segment generator and normalize its cues.

        The faster-whisper segments are a generator; they are eagerly
        materialized here so no lazy generator can escape into a
        :class:`TranscriptResult`.
        """
        try:
            raw_segments = list(transcription)
        except Exception as exc:
            raise AsrError(_transcribe_remediation(self._model, media_path)) from exc
        normalized: list[Segment] = []
        for raw in raw_segments:
            text = " ".join(raw.text.split())
            if not text:
                continue
            segment = Segment(
                start_seconds=float(raw.start),
                end_seconds=float(raw.end),
                text=text,
            )
            if segment.end_seconds < segment.start_seconds:
                raise AsrError(_monotonic_remediation(media_path))
            normalized.append(segment)
        if not normalized:
            raise AsrError(_empty_remediation(media_path))
        for previous, current in itertools.pairwise(normalized):
            if current.start_seconds < previous.start_seconds:
                raise AsrError(_monotonic_remediation(media_path))
        return tuple(normalized)
