"""Validated runtime settings for the downloader.

Only loopback Ollama endpoints are permitted: every remote host and any
credential-bearing URL is rejected, so transcript data never leaves this
machine. No API keys or secrets are accepted anywhere in these settings.
"""

from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from youtube_downloader.models import SettingsError

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "::1"})

_DEFAULT_OUTPUT_ROOT = "downloads"
_DEFAULT_WHISPER_MODEL = "small"
_DEFAULT_WHISPER_DEVICE = "cpu"
_DEFAULT_WHISPER_COMPUTE_TYPE = "int8"
_DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_MODEL = "llama3.2"


def _is_loopback(hostname: str | None) -> bool:
    """True when *hostname* resolves to a loopback address only."""
    if hostname is None:
        return False
    host = hostname.lower().rstrip(".")
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_ollama_endpoint(endpoint: str) -> None:
    if not endpoint or endpoint != endpoint.strip():
        raise SettingsError(f"ollama_endpoint must be a non-blank URL, got {endpoint!r}")
    parsed = urlsplit(endpoint)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise SettingsError(
            f"ollama_endpoint must be an http(s) URL with a host, got {endpoint!r}"
        )
    if parsed.username is not None or parsed.password is not None:
        raise SettingsError("ollama_endpoint must not embed user-info credentials")
    if not _is_loopback(parsed.hostname):
        raise SettingsError(
            "ollama_endpoint host "
            + f"{parsed.hostname!r} is not loopback; only "
            + "127.0.0.0/8, localhost, and ::1 are allowed"
        )


def _require_non_blank(value: str, name: str) -> None:
    if not value.strip():
        raise SettingsError(f"{name} must not be blank")


@dataclass(frozen=True)
class Settings:
    """Immutable, validated configuration for the downloader.

    ``ollama_endpoint`` accepts only loopback hosts (``127.0.0.0/8``,
    ``localhost``, ``::1``) over http(s), with no user-info credentials;
    anything else raises :class:`SettingsError` at construction time.
    """

    output_root: Path = field(default_factory=lambda: Path(_DEFAULT_OUTPUT_ROOT))
    whisper_model: str = _DEFAULT_WHISPER_MODEL
    whisper_device: str = _DEFAULT_WHISPER_DEVICE
    whisper_compute_type: str = _DEFAULT_WHISPER_COMPUTE_TYPE
    ollama_endpoint: str = _DEFAULT_OLLAMA_ENDPOINT
    ollama_model: str = _DEFAULT_OLLAMA_MODEL

    def __post_init__(self) -> None:
        _validate_ollama_endpoint(self.ollama_endpoint)
        _require_non_blank(self.whisper_model, "whisper_model")
        _require_non_blank(self.whisper_device, "whisper_device")
        _require_non_blank(self.whisper_compute_type, "whisper_compute_type")
        _require_non_blank(self.ollama_model, "ollama_model")
