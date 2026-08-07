"""Command-line entry point for the youtube-downloader package.

This module is the packaging scaffold (plan Todo 1): it wires argparse so
that both ``python main.py`` and ``python -m youtube_downloader`` expose
identical ``--help`` output. The full interactive download flow is
implemented in plan Todo 8.
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-downloader",
        description="Download YouTube media and generate local transcripts and summaries.",
    )
    _ = parser.add_argument("url", nargs="?", help="YouTube video URL to download")
    return parser


def main() -> None:
    parser = build_parser()
    _ = parser.parse_args()
