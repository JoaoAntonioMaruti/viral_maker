#!/usr/bin/env python3
"""Download the audio from a TikTok URL into the project's audio folder."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse


DEFAULT_AUDIO_DIRECTORY = Path("audio")


class AudioDownloadError(RuntimeError):
    """An expected, user-facing download error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download only the audio from a TikTok video."
    )
    parser.add_argument("url", help="TikTok video URL")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_AUDIO_DIRECTORY,
        help="destination directory (default: audio)",
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help="browser cookies for restricted videos, e.g. firefox or chrome",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing audio file for the same TikTok video",
    )
    return parser


def validate_tiktok_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"}:
        raise AudioDownloadError("URL must start with http:// or https://")
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        raise AudioDownloadError("URL must belong to tiktok.com")
    return url


def require_program(name: str) -> str:
    program = shutil.which(name)
    if program is None:
        raise AudioDownloadError(f"Required program not found: {name}")
    return program


def build_download_command(
    yt_dlp: str,
    url: str,
    output_directory: Path,
    report_file: Path,
    *,
    overwrite: bool = False,
    cookies_from_browser: str | None = None,
) -> list[str]:
    command = [
        yt_dlp,
        "--no-playlist",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--paths",
        str(output_directory),
        "--output",
        "%(id)s.%(ext)s",
        "--newline",
        "--progress",
        "--progress-delta",
        "0.5",
        "--progress-template",
        "download:Baixando: %(progress._percent_str)s | %(progress._speed_str)s | ETA %(progress._eta_str)s",
        "--print-to-file",
        "after_move:filepath",
        str(report_file),
        "--force-overwrites" if overwrite else "--no-overwrites",
    ]
    if cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    command.append(url)
    return command


def download_audio(
    url: str,
    output_directory: Path = DEFAULT_AUDIO_DIRECTORY,
    *,
    overwrite: bool = False,
    cookies_from_browser: str | None = None,
) -> Path:
    validate_tiktok_url(url)
    yt_dlp = require_program("yt-dlp")
    require_program("ffmpeg")
    require_program("ffprobe")

    destination = output_directory.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tiktok-audio-") as temp_directory:
        report_file = Path(temp_directory) / "downloaded-path.txt"
        command = build_download_command(
            yt_dlp,
            url,
            destination,
            report_file,
            overwrite=overwrite,
            cookies_from_browser=cookies_from_browser,
        )
        print("Iniciando download do áudio do TikTok...", flush=True)
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:
            raise AudioDownloadError(
                f"yt-dlp could not download the TikTok audio (exit code {exc.returncode})"
            ) from exc
        try:
            report = report_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise AudioDownloadError(
                "yt-dlp finished without reporting an audio file"
            ) from exc

    printed_paths = [line.strip() for line in report.splitlines() if line.strip()]
    if not printed_paths:
        raise AudioDownloadError("yt-dlp finished without reporting an audio file")
    downloaded = Path(printed_paths[-1]).expanduser().resolve()
    if downloaded.parent != destination or not downloaded.is_file():
        raise AudioDownloadError(f"Downloaded audio file was not found: {downloaded}")
    return downloaded


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        downloaded = download_audio(
            args.url,
            args.output_directory,
            overwrite=args.overwrite,
            cookies_from_browser=args.cookies_from_browser,
        )
    except AudioDownloadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Downloaded: {downloaded}")
    print(f"Use it with: python3 video_maker.py --music {downloaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
