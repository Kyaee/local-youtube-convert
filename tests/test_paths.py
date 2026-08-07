"""Tests for artifact path layout and safe-title sanitation (plan Todo 2)."""

from pathlib import Path

import pytest

from youtube_downloader.models import Format
from youtube_downloader.paths import ArtifactPaths, artifact_paths, safe_title, video_dir

TRAVERSAL_TITLES = (
    "../evil",
    "..",
    "../..",
    "/etc/passwd",
    "a/../../b",
    "..\\evil",
    "C:\\Windows\\system32",
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Never Gonna Give You Up", "Never Gonna Give You Up"),
        ("日本語 タイトル", "日本語 タイトル"),
        ('a/b\\c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j"),
        ("  leading and trailing  ", "leading and trailing"),
        ("..", ""),
        ("../..", ""),
        ("/etc/passwd", "etc_passwd"),
        ("a  b\tc\n", "a b c"),
        ("", ""),
    ],
)
def test_safe_title(title: str, expected: str) -> None:
    assert safe_title(title) == expected


@pytest.mark.parametrize("title", TRAVERSAL_TITLES)
def test_safe_title_never_contains_a_separator(title: str) -> None:
    result = safe_title(title)
    assert "/" not in result
    assert "\\" not in result
    assert result != ".."


@pytest.mark.parametrize("title", TRAVERSAL_TITLES)
def test_video_dir_never_escapes_output_root(tmp_path: Path, title: str) -> None:
    directory = video_dir(tmp_path, title, "abc123")
    assert directory.parent == tmp_path
    assert "/" not in directory.name
    assert "\\" not in directory.name


def test_video_dir_layout(tmp_path: Path) -> None:
    assert video_dir(tmp_path, "My Video", "abc123") == tmp_path / "My Video [abc123]"


def test_blank_title_falls_back_to_video_id(tmp_path: Path) -> None:
    assert video_dir(tmp_path, "", "abc123") == tmp_path / "abc123 [abc123]"
    assert video_dir(tmp_path, "../..", "abc123") == tmp_path / "abc123 [abc123]"


def test_video_dir_is_id_stable_and_collision_free(tmp_path: Path) -> None:
    first = video_dir(tmp_path, "Same Title", "abc123")
    second = video_dir(tmp_path, "Same Title", "abc123")
    other = video_dir(tmp_path, "Same Title", "xyz789")
    assert first == second
    assert other != first


def test_artifact_paths_exact_filenames(tmp_path: Path) -> None:
    paths = artifact_paths(tmp_path, "My Video", "abc123", Format.MP4)
    assert isinstance(paths, ArtifactPaths)
    assert paths.directory == tmp_path / "My Video [abc123]"
    assert paths.media == tmp_path / "My Video [abc123]" / "My Video [abc123].mp4"
    assert paths.transcript == tmp_path / "My Video [abc123]" / "transcript.md"
    assert paths.summary == tmp_path / "My Video [abc123]" / "summary.md"
    assert paths.metadata == tmp_path / "My Video [abc123]" / "metadata.json"


def test_media_extension_depends_on_format(tmp_path: Path) -> None:
    mp3 = artifact_paths(tmp_path, "T", "abc123", Format.MP3).media
    mp4 = artifact_paths(tmp_path, "T", "abc123", Format.MP4).media
    assert mp3.suffix == ".mp3"
    assert mp4.suffix == ".mp4"
    assert mp3 != mp4


def test_artifacts_live_inside_the_video_directory(tmp_path: Path) -> None:
    paths = artifact_paths(tmp_path, "T", "abc123", Format.MP4)
    for artifact in (paths.media, paths.transcript, paths.summary, paths.metadata):
        assert artifact.parent == paths.directory


@pytest.mark.parametrize("title", TRAVERSAL_TITLES)
def test_artifact_paths_never_escape_output_root(tmp_path: Path, title: str) -> None:
    paths = artifact_paths(tmp_path, title, "abc123", Format.MP4)
    assert paths.directory.parent == tmp_path
    for artifact in (paths.media, paths.transcript, paths.summary, paths.metadata):
        assert artifact.parent == paths.directory
