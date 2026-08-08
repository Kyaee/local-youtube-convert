# youtube-downloader

Download one public YouTube video, transcribe it, and create a local summary.
YouTube is contacted for video metadata, media, and captions. “Local” means that
caption fallback, speech-to-text, and summarization run on this machine; this is
not an offline YouTube downloader.

## Setup

Install `uv`, then from the project directory synchronize the environment:

```bash
.venv/bin/uv sync --all-groups
```

Check that FFmpeg and `ffprobe` are available before downloading:

```bash
ffmpeg -version
```

If that command is missing, install FFmpeg and retry the check. On Debian or
Ubuntu use `sudo apt install ffmpeg`; on macOS use `brew install ffmpeg`. Other
platforms can use the packages listed at <https://ffmpeg.org/download.html>.
No particular FFmpeg version is required by this guide.

Summaries require Ollama locally. Install Ollama using its platform instructions
(on Linux, the official installer is `curl -fsSL https://ollama.com/install.sh | sh`),
start its loopback service, and pull the default model:

```bash
ollama serve
ollama pull llama3.2
```

The default endpoint is `http://127.0.0.1:11434` and the default model is
`llama3.2`. Only loopback Ollama endpoints are accepted; the application does not
send transcript text to a remote summarization service. If Ollama is not running,
start it with `ollama serve`. If the model is missing, run `ollama pull llama3.2`.

## Run

Activate the environment or use its interpreter, then run the interactive CLI:

```bash
python main.py
```

The equivalent module invocation is:

```bash
python -m youtube_downloader
```

Enter one YouTube video URL, choose `mp3` or `mp4`, and optionally enter a
summary goal. Press Enter at the goal prompt to use the default key-points goal.
The request is one video in one format; playlist handling is not supported.

When captions are available, they are used first. If usable captions are not
available, faster-whisper transcribes the downloaded media locally. The default
Whisper configuration is model `small`, CPU device, and `int8` compute type, so
no GPU is required. The first faster-whisper use downloads the configured model
from Hugging Face and caches it locally. This can take time and consume
substantial disk space; leave room in the local model cache and retry if the
download is interrupted or the cache is corrupt. A missing faster-whisper
package is repaired by running `.venv/bin/uv sync --all-groups` again.

## Output

Each video is written below `downloads/` by default:

```text
downloads/<safe-title> [<youtube-id>]/
├── <safe-title> [<youtube-id>].mp3  # or .mp4
├── transcript.md
├── summary.md
└── metadata.json
```

The title is made filesystem-safe and the stable YouTube ID prevents collisions.
`metadata.json` records the requested format, artifact paths, transcript source,
models, status, completed stage, retained paths, and errors.

`status: complete` means media, `transcript.md`, `summary.md`, and
`metadata.json` were written. `status: partial` means media was retained but a
transcript or summary stage failed; inspect `metadata.json` and its
`retained_paths` entries. Missing captions fall back to ASR, but silent media,
failed model downloads, and transcription errors can still leave a partial
result. A missing Ollama service or model similarly prevents a complete summary
and preserves the paths already produced. Media acquisition failures happen
before workflow metadata is written, so retry after correcting the URL, network,
FFmpeg installation, or video availability.

## Limits And Recovery

YouTube availability is outside this program's control. Videos can be restricted,
private, members-only, sign-in-gated, removed, or otherwise unavailable and cannot
be guaranteed. The downloader does not bypass those restrictions. Check the URL and
network, then retry with a different public video when YouTube reports one of
these conditions. A transient network or rate-limit failure is retried a limited
number of times; a persistent failure should be retried later.

## Development

```bash
.venv/bin/uv run pytest
.venv/bin/uv run ruff check .
.venv/bin/uv run basedpyright
```
