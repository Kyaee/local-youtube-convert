from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
README = PROJECT_ROOT / "README.md"

REQUIRED_TEXT = (
    ".venv/bin/uv sync --all-groups",
    "python main.py",
    "python -m youtube_downloader",
    "ffmpeg -version",
    "sudo apt install ffmpeg",
    "brew install ffmpeg",
    "ollama serve",
    "ollama pull llama3.2",
    "llama3.2",
    "faster-whisper",
    "Hugging Face",
    "CPU",
    "int8",
    "transcript.md",
    "summary.md",
    "metadata.json",
    "status: complete",
    "status: partial",
    "retained_paths",
    "restricted",
    "private",
    "unavailable",
    "summary goal",
)

FORBIDDEN_INSTRUCTIONS = (
    "api key",
    "api-key",
    "openai",
    "anthropic",
    "aws",
    "google cloud",
    "azure",
)


def test_readme_documents_required_local_operations() -> None:
    readme = README.read_text(encoding="utf-8")

    for required in REQUIRED_TEXT:
        assert required in readme, f"README missing required documentation: {required!r}"


@pytest.mark.parametrize("forbidden", FORBIDDEN_INSTRUCTIONS)
def test_readme_has_no_api_key_or_cloud_provider_setup(forbidden: str) -> None:
    readme = README.read_text(encoding="utf-8").lower()

    assert forbidden not in readme
