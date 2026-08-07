"""Tests for yt-dlp-backed media acquisition (plan Todo 3).

All tests are hermetic: yt-dlp is faked with a context-managed :class:`FakeYdl`
that records every ``(options, url, download)`` call, and FFmpeg presence is
injected (the machine has no real FFmpeg and it is never invoked). A missing
behavior -- wrong option map, non-ID-bearing template, guessed final path,
unmapped error, or skipped retry -- fails exactly one test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from youtube_downloader.media import YtDlpMediaDownloader
from youtube_downloader.models import (
    DownloadRequest,
    Format,
    InvalidURLError,
    MediaDownloader,
    MediaDownloadError,
    MediaResult,
    MissingDependencyError,
)

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"
TITLE = "Never Gonna Give You Up"

METADATA: dict[str, object] = {
    "id": VIDEO_ID,
    "title": TITLE,
    "uploader": "Rick Astley",
    "duration": 213.5,
    "upload_date": "20091025",
    "view_count": 1_234_567,
    "description": "A classic.",
}

MP4_DEST = "T [abc123]/T [abc123].mp4"
MP3_DEST = "T [abc123]/T [abc123].mp3"


class FakeYdl:
    """Minimal context-managed stand-in for ``yt_dlp.YoutubeDL``."""

    def __init__(
        self,
        options: dict[str, object],
        script: Callable[[str, bool], object],
        log: list[tuple[dict[str, object], str, bool]],
    ) -> None:
        self.options = options
        self._script = script
        self._log = log

    def __enter__(self) -> FakeYdl:
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_tb: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> object:
        self._log.append((self.options, url, download))
        return self._script(url, download)


class FakeFactory:
    """Builds one :class:`FakeYdl` per yt-dlp call and records every call."""

    def __init__(self, script: Callable[[str, bool], object]) -> None:
        self._script = script
        self.log: list[tuple[dict[str, object], str, bool]] = []

    def __call__(self, options: Mapping[str, object]) -> FakeYdl:
        return FakeYdl(dict(options), self._script, self.log)


def success_script(final: Path) -> Callable[[str, bool], object]:
    """Scripted extract_info: metadata first, then a completed download."""

    def script(url: str, download: bool = True) -> object:
        if download:
            return {**METADATA, "requested_downloads": [{"filepath": str(final)}]}
        return dict(METADATA)

    return script


def touch(path: Path) -> Path:
    """Create *path* (with parents) as a real file on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    return path


def make_downloader(
    factory: FakeFactory,
    *,
    sleeps: list[float] | None = None,
    ffmpeg: bool = True,
) -> YtDlpMediaDownloader:
    return YtDlpMediaDownloader(
        ydl_factory=factory,
        sleep=sleeps.append if sleeps is not None else (lambda _seconds: None),
        ffmpeg_available=(lambda: True) if ffmpeg else (lambda: False),
    )


def test_ytdl_downloader_satisfies_protocol() -> None:
    assert isinstance(YtDlpMediaDownloader(), MediaDownloader)


def test_mp3_and_mp4_option_maps_differ(tmp_path: Path) -> None:
    mp4_dest = tmp_path / MP4_DEST
    touch(mp4_dest)
    mp4_factory = FakeFactory(success_script(mp4_dest))
    make_downloader(mp4_factory).download(DownloadRequest(URL, Format.MP4), mp4_dest)
    mp4_opts = mp4_factory.log[1][0]
    assert mp4_opts["format"] == "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"
    assert mp4_opts["merge_output_format"] == "mp4"
    assert "postprocessors" not in mp4_opts

    mp3_dest = tmp_path / MP3_DEST
    touch(mp3_dest)
    mp3_factory = FakeFactory(success_script(mp3_dest))
    make_downloader(mp3_factory).download(DownloadRequest(URL, Format.MP3), mp3_dest)
    mp3_opts = mp3_factory.log[1][0]
    assert mp3_opts["postprocessors"] == [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
    ]
    assert "merge_output_format" not in mp3_opts
    assert mp3_opts["format"] != mp4_opts["format"]


def test_outtmpl_points_at_requested_destination(tmp_path: Path) -> None:
    mp4_dest = tmp_path / MP4_DEST
    touch(mp4_dest)
    mp4_factory = FakeFactory(success_script(mp4_dest))
    make_downloader(mp4_factory).download(DownloadRequest(URL, Format.MP4), mp4_dest)
    mp4_outtmpl = mp4_factory.log[1][0]["outtmpl"]
    assert mp4_outtmpl == str(mp4_dest)
    assert "[abc123]" in mp4_outtmpl

    mp3_dest = tmp_path / MP3_DEST
    touch(mp3_dest)
    mp3_factory = FakeFactory(success_script(mp3_dest))
    make_downloader(mp3_factory).download(DownloadRequest(URL, Format.MP3), mp3_dest)
    mp3_outtmpl = mp3_factory.log[1][0]["outtmpl"]
    assert mp3_outtmpl == str(mp3_dest.with_suffix(""))
    assert "[abc123]" in mp3_outtmpl


def test_metadata_extracted_before_download(tmp_path: Path) -> None:
    final = tmp_path / MP4_DEST
    touch(final)
    factory = FakeFactory(success_script(final))

    result = make_downloader(factory).download(DownloadRequest(URL, Format.MP4), final)

    assert [download for _opts, _url, download in factory.log] == [False, True]
    assert factory.log[0][0]["skip_download"] is True
    assert isinstance(result, MediaResult)
    assert result.final_path == final
    assert result.video_id == VIDEO_ID
    assert result.title == TITLE
    assert result.uploader == "Rick Astley"
    assert result.duration_seconds == 213.5
    assert result.upload_date == "20091025"
    assert result.view_count == 1_234_567
    assert result.description == "A classic."


def test_final_path_is_postprocessed_path_not_guess(tmp_path: Path) -> None:
    # The outtmpl stem points at the directory, but the FFmpegExtractAudio
    # postprocessor produced the real .mp3 file; requested_downloads must be
    # the source of truth, never the pre-merge destination guess.
    final = tmp_path / "T [abc123].mp3"
    touch(final)
    factory = FakeFactory(success_script(final))
    destination = tmp_path / MP3_DEST

    result = make_downloader(factory).download(DownloadRequest(URL, Format.MP3), destination)

    assert result.final_path == final
    assert result.final_path != destination
    assert result.final_path.is_file()


def test_extract_metadata_seam_never_downloads() -> None:
    factory = FakeFactory(success_script(Path("/unused")))

    info = make_downloader(factory).extract_metadata(URL)

    assert info["id"] == VIDEO_ID
    assert [download for _opts, _url, download in factory.log] == [False]


def test_unsupported_url_raises_invalid_url_error(tmp_path: Path) -> None:
    def script(url: str, download: bool = True) -> object:
        raise DownloadError("Unsupported URL: https://example.com")

    factory = FakeFactory(script)
    with pytest.raises(InvalidURLError, match="Unsupported URL"):
        make_downloader(factory).download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)
    assert len(factory.log) == 1  # metadata phase failed, no retry, no download


def test_fake_download_error_raises_media_download_error(tmp_path: Path) -> None:
    def script(url: str, download: bool = True) -> object:
        if download:
            raise DownloadError("ERROR: Video unavailable. This video is unavailable")
        return dict(METADATA)

    factory = FakeFactory(script)
    with pytest.raises(MediaDownloadError, match="unavailable"):
        make_downloader(factory).download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)
    assert [download for _opts, _url, download in factory.log] == [False, True]


def test_nonzero_return_raises_media_download_error(tmp_path: Path) -> None:
    def script(url: str, download: bool = True) -> object:
        return dict(METADATA) if not download else 42  # yt-dlp style nonzero retcode

    factory = FakeFactory(script)
    with pytest.raises(MediaDownloadError, match="unexpected result"):
        make_downloader(factory).download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)


def test_ffmpeg_failure_with_ffmpeg_present_is_not_misdiagnosed(tmp_path: Path) -> None:
    # FFmpeg is present (mocked) yet the conversion fails: the error must be a
    # MediaDownloadError, never a MissingDependencyError, and never a success.
    def script(url: str, download: bool = True) -> object:
        if download:
            raise DownloadError("ERROR: Postprocessing: ffmpeg conversion failed")
        return dict(METADATA)

    factory = FakeFactory(script)
    with pytest.raises(MediaDownloadError):
        make_downloader(factory).download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)
    assert [download for _opts, _url, download in factory.log] == [False, True]


@pytest.mark.parametrize(
    "download_info",
    [
        dict(METADATA),  # no requested_downloads at all
        {**METADATA, "requested_downloads": []},
        {**METADATA, "requested_downloads": [{"filepath": ""}]},
        {**METADATA, "requested_downloads": [{"filepath": "/nonexistent/ghost.mp4"}]},
    ],
)
def test_no_real_final_file_never_succeeds(
    tmp_path: Path, download_info: dict[str, object]
) -> None:
    def script(url: str, download: bool = True) -> object:
        return dict(METADATA) if not download else dict(download_info)

    factory = FakeFactory(script)
    with pytest.raises(MediaDownloadError):
        make_downloader(factory).download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)


def test_retry_transient_failure_succeeds_on_second_attempt(tmp_path: Path) -> None:
    final = tmp_path / "v.mp4"
    touch(final)
    sleeps: list[float] = []
    download_attempts = 0

    def script(url: str, download: bool = True) -> object:
        nonlocal download_attempts
        if not download:
            return dict(METADATA)
        download_attempts += 1
        if download_attempts == 1:
            raise DownloadError("ERROR: unable to download video data: Connection reset by peer")
        return {**METADATA, "requested_downloads": [{"filepath": str(final)}]}

    factory = FakeFactory(script)
    result = make_downloader(factory, sleeps=sleeps).download(
        DownloadRequest(URL, Format.MP4), final
    )

    assert result.final_path == final
    assert download_attempts == 2
    assert [download for _opts, _url, download in factory.log] == [False, True, True]
    assert sleeps == [1.0]  # exactly one bounded delay, no busy loop


def test_permanent_download_error_is_not_retried(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def script(url: str, download: bool = True) -> object:
        if download:
            raise DownloadError("ERROR: Video unavailable. This video is unavailable")
        return dict(METADATA)

    factory = FakeFactory(script)
    with pytest.raises(MediaDownloadError):
        make_downloader(factory, sleeps=sleeps).download(
            DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST
        )
    assert [download for _opts, _url, download in factory.log] == [False, True]
    assert sleeps == []  # permanent errors surface immediately


def test_missing_ffmpeg_raises_before_any_ytdl_call(tmp_path: Path) -> None:
    factory = FakeFactory(success_script(tmp_path / "v.mp4"))
    with pytest.raises(MissingDependencyError, match="FFmpeg"):
        make_downloader(factory, ffmpeg=False).download(
            DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST
        )
    assert factory.log == []


def test_default_ffmpeg_check_uses_shutil_which(mocker, tmp_path: Path) -> None:
    import youtube_downloader.media as media_module

    mocker.patch.object(media_module.shutil, "which", return_value=None)
    factory = FakeFactory(success_script(tmp_path / "v.mp4"))
    downloader = YtDlpMediaDownloader(ydl_factory=factory)

    with pytest.raises(MissingDependencyError, match="FFmpeg"):
        downloader.download(DownloadRequest(URL, Format.MP4), tmp_path / MP4_DEST)
    assert factory.log == []


def test_options_never_use_ignoreerrors_or_bypasses(tmp_path: Path) -> None:
    mp4_dest = tmp_path / MP4_DEST
    touch(mp4_dest)
    mp4_factory = FakeFactory(success_script(mp4_dest))
    make_downloader(mp4_factory).download(DownloadRequest(URL, Format.MP4), mp4_dest)

    mp3_dest = tmp_path / MP3_DEST
    touch(mp3_dest)
    mp3_factory = FakeFactory(success_script(mp3_dest))
    make_downloader(mp3_factory).download(DownloadRequest(URL, Format.MP3), mp3_dest)

    all_options = [entry[0] for entry in mp4_factory.log + mp3_factory.log]
    for options in all_options:
        assert options.get("ignoreerrors") is not True
        assert "cookiesfrombrowser" not in options
        assert "cookiefile" not in options
        assert "no_check_certificate" not in options
