"""Per-video artifact layout: directories and exact filenames.

Every video lands in ``<output-root>/<safe-title> [<youtube-id>]/`` with the
exact artifacts ``<safe-title> [<youtube-id>].<ext>``, ``transcript.md``,
``summary.md``, and ``metadata.json``.

Collision strategy: the directory name embeds the stable youtube id, so two
different videos that share a title always get distinct directories, while a
re-download of the same video maps to the same directory (its own artifacts
may then be deliberately refreshed, never a different video's). Because
``safe_title`` strips every path separator, no path can escape ``output_root``.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from youtube_downloader.models import Format

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_WHITESPACE_RUN = re.compile(r"\s+")
_UNDERSCORE_RUN = re.compile(r"_+")


def safe_title(title: str) -> str:
    """Return a filesystem-safe rendering of *title* (may be ``""``).

    Filesystem-unsafe characters (both path separators, Windows
    metacharacters, and control characters) become ``_``; whitespace and
    underscore runs are collapsed; leading/trailing dots, spaces, and
    underscores are stripped. A title that reduces to nothing returns
    ``""`` so callers can fall back to the video id.
    """
    cleaned = _WHITESPACE_RUN.sub(" ", title)
    cleaned = _UNSAFE_CHARS.sub("_", cleaned)
    cleaned = _UNDERSCORE_RUN.sub("_", cleaned)
    return cleaned.strip(" ._")


def _directory_name(title: str, video_id: str) -> str:
    safe = safe_title(title) or video_id
    return f"{safe} [{video_id}]"


def video_dir(output_root: Path, title: str, video_id: str) -> Path:
    """Return ``<output-root>/<safe-title> [<youtube-id>]``.

    A title that sanitizes to nothing falls back to ``video_id`` as the safe
    title. Because ``safe_title`` strips every path separator, the result is
    always a direct child of ``output_root``.
    """
    return output_root / _directory_name(title, video_id)


def _media_extension(fmt: Format) -> str:
    match fmt:
        case Format.MP3:
            return ".mp3"
        case Format.MP4:
            return ".mp4"


@dataclass(frozen=True)
class ArtifactPaths:
    """Exact artifact filenames for one video directory."""

    directory: Path
    media: Path
    transcript: Path
    summary: Path
    metadata: Path


def artifact_paths(output_root: Path, title: str, video_id: str, fmt: Format) -> ArtifactPaths:
    """Return the full artifact layout for one video.

    Four independent inputs are required: where to write, the display title,
    the stable youtube id, and the selected format (which determines the media
    extension). The media file shares the directory stem, so
    ``media.parent == directory`` always holds.
    """
    name = _directory_name(title, video_id)
    directory = output_root / name
    return ArtifactPaths(
        directory=directory,
        media=directory / f"{name}{_media_extension(fmt)}",
        transcript=directory / "transcript.md",
        summary=directory / "summary.md",
        metadata=directory / "metadata.json",
    )
