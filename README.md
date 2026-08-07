# youtube-downloader

Local YouTube downloader: fetch media, transcribe it, and summarize it — all on-device.

## Status

This is the packaging scaffold (plan Todo 1): a typed `src`-layout Python project
managed with `uv`, exposing an argparse CLI shell. Setup, operations, and limits
documentation land in a later iteration (plan Todo 9).

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run basedpyright
```
