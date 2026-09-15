#!/usr/bin/env python3
"""Create a captioned social video by concatenating two clips with FFmpeg."""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from download_tiktok_audio import AudioDownloadError, download_audio


DEFAULT_FIRST = Path("videos/1.mp4")
DEFAULT_FINAL_DIRECTORY = Path("videos/final_videos")
DEFAULT_DATA = Path("data.json")
DEFAULT_OUTPUT_DIRECTORY = Path("outputs")
DEFAULT_MUSIC = Path("audio/ssstik.io_1789458727141.mp3")
LANGUAGES = ("en", "pt", "ja")
POSITIONS = ("top", "center", "bottom")


class VideoMakerError(RuntimeError):
    """An expected, user-facing error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Join two vertical videos and caption the first one."
    )
    parser.add_argument(
        "--language",
        choices=LANGUAGES,
        default="en",
        help="caption language (default: en)",
    )
    parser.add_argument(
        "--index",
        type=int,
        help="1-based caption index; omit to choose randomly",
    )
    parser.add_argument(
        "--position",
        choices=POSITIONS,
        default="top",
        help="caption position (default: top)",
    )
    parser.add_argument(
        "--carousel",
        action="store_true",
        help="add the localized carousel prompt to the final video",
    )
    parser.add_argument(
        "--carousel-position",
        choices=POSITIONS,
        default="top",
        help="carousel prompt position (default: top)",
    )
    parser.add_argument("--first", type=Path, default=DEFAULT_FIRST)
    parser.add_argument(
        "--final",
        type=int,
        default=1,
        metavar="NUMBER",
        help="final video number from --final-directory (default: 1)",
    )
    parser.add_argument(
        "--final-directory", type=Path, default=DEFAULT_FINAL_DIRECTORY
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    music_group = parser.add_mutually_exclusive_group()
    music_group.add_argument(
        "--music",
        type=Path,
        help="use a specific background music file",
    )
    music_group.add_argument(
        "--music-url",
        help="download and use audio from a TikTok URL without prompting",
    )
    parser.add_argument(
        "--music-volume",
        type=float,
        default=1.0,
        metavar="0..1",
        help="background music volume (default: 1.0 / 100%%)",
    )
    music_group.add_argument(
        "--with-music",
        action="store_true",
        help=f"use the default background music ({DEFAULT_MUSIC}) without prompting",
    )
    music_group.add_argument(
        "--no-music",
        action="store_true",
        help="generate a silent video without prompting",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output path (default: outputs/{timestamp}_{video_number}.mp4)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the output file if it already exists",
    )
    return parser


def ensure_program(name: str) -> str:
    program = shutil.which(name)
    if program is None:
        raise VideoMakerError(f"Required program not found: {name}")
    return program


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise VideoMakerError(f"{label} file not found: {path}")
    return resolved


def list_final_videos(directory: Path) -> list[tuple[int, Path]]:
    resolved_directory = directory.expanduser().resolve()
    if not resolved_directory.is_dir():
        raise VideoMakerError(f"Final video directory not found: {directory}")

    videos = []
    for path in resolved_directory.glob("*.mp4"):
        if path.stem.isdigit() and int(path.stem) >= 1:
            videos.append((int(path.stem), path.resolve()))
    videos.sort(key=lambda item: item[0])
    return videos


def resolve_final_video(directory: Path, number: int) -> Path:
    if number < 1:
        raise VideoMakerError("Final video number must be 1 or greater")
    available = dict(list_final_videos(directory))
    if number not in available:
        choices = ", ".join(str(value) for value in available) or "none"
        raise VideoMakerError(
            f"Final video {number} not found in {directory}. Available: {choices}"
        )
    return available[number]


def prompt_for_music(input_fn=None) -> bool:
    if input_fn is None:
        input_fn = input
    while True:
        try:
            answer = input_fn("Adicionar música de fundo? [Y/n] ").strip().lower()
        except EOFError:
            return True
        if answer in {"", "y", "yes", "s", "sim"}:
            return True
        if answer in {"n", "no", "nao", "não"}:
            return False
        print("Responda com Y/sim ou n/não.", file=sys.stderr)


def prompt_for_music_url(input_fn=None) -> str:
    if input_fn is None:
        input_fn = input
    while True:
        try:
            url = input_fn("URL do TikTok: ").strip()
        except EOFError as exc:
            raise AudioDownloadError("TikTok URL was not provided") from exc
        if url:
            return url
        print("Informe a URL do TikTok.", file=sys.stderr)


def resolve_cli_music(
    args: argparse.Namespace,
    input_fn=None,
    downloader: Callable[..., Path] = download_audio,
) -> Path | None:
    if args.music is not None:
        return args.music
    if args.music_url is not None:
        return downloader(args.music_url)
    if args.with_music:
        return DEFAULT_MUSIC
    if args.no_music:
        return None
    if not prompt_for_music(input_fn):
        return None
    return downloader(prompt_for_music_url(input_fn))


def load_captions(path: Path, language: str) -> list[str]:
    try:
        with path.open(encoding="utf-8") as source:
            document = json.load(source)
    except json.JSONDecodeError as exc:
        raise VideoMakerError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    except OSError as exc:
        raise VideoMakerError(f"Could not read {path}: {exc}") from exc

    try:
        captions = document[language]["data"]
    except (KeyError, TypeError) as exc:
        raise VideoMakerError(
            f"Missing data for language '{language}' in {path}"
        ) from exc

    if not isinstance(captions, list) or not captions:
        raise VideoMakerError(f"Language '{language}' has no captions in {path}")
    if not all(isinstance(caption, str) and caption.strip() for caption in captions):
        raise VideoMakerError(
            f"Language '{language}' must contain only non-empty caption strings"
        )
    return captions


def load_carousel_caption(path: Path, language: str) -> str:
    try:
        with path.open(encoding="utf-8") as source:
            document = json.load(source)
    except json.JSONDecodeError as exc:
        raise VideoMakerError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    except OSError as exc:
        raise VideoMakerError(f"Could not read {path}: {exc}") from exc

    try:
        caption = document[language]["carousel"]
    except (KeyError, TypeError) as exc:
        raise VideoMakerError(
            f"Missing carousel caption for language '{language}' in {path}"
        ) from exc
    if not isinstance(caption, str) or not caption.strip():
        raise VideoMakerError(
            f"Carousel caption for language '{language}' must be a non-empty string"
        )
    return caption


def select_caption(captions: Sequence[str], index: int | None) -> tuple[int, str]:
    if index is None:
        selected = random.randrange(len(captions))
    else:
        if index < 1 or index > len(captions):
            raise VideoMakerError(
                f"Caption index must be between 1 and {len(captions)}"
            )
        selected = index - 1
    return selected + 1, captions[selected]


def default_output_path(first: Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    video_number = first.stem
    return DEFAULT_OUTPUT_DIRECTORY / f"{timestamp}_{video_number}.mp4"


def probe_duration(ffprobe: str, video: Path) -> float:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video),
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
        duration = float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as exc:
        details = getattr(exc, "stderr", "").strip()
        suffix = f": {details}" if details else ""
        raise VideoMakerError(f"Could not determine duration of {video}{suffix}") from exc
    if duration <= 0:
        raise VideoMakerError(f"Video has an invalid duration: {video}")
    return duration


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(1, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", r"\N")
        .replace("\n", r"\N")
        .replace("\r", r"\N")
    )


def create_ass(caption: str, duration: float, position: str) -> str:
    # Margins leave room for common social-network UI overlays.
    alignment = {"top": 8, "center": 5, "bottom": 2}[position]
    vertical_margin = {"top": 170, "center": 0, "bottom": 300}[position]
    end = ass_timestamp(duration)
    text = escape_ass_text(caption)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Social,Noto Sans CJK JP,68,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,{alignment},90,90,{vertical_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{end},Social,,0,0,0,,{text}
"""


def escape_filter_path(path: Path) -> str:
    # FFmpeg parses filter arguments even when subprocess avoids a shell.
    return str(path).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def make_video(
    ffmpeg: str,
    first: Path,
    final: Path,
    subtitle: Path,
    output: Path,
    overwrite: bool,
    final_subtitle: Path | None = None,
    music: Path | None = None,
    music_volume: float = 1.0,
) -> None:
    subtitle_filter = escape_filter_path(subtitle)
    normalize = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920:(in_w-out_w)/2:(in_h-out_h)/2,"
        "fps=60,setsar=1,setpts=PTS-STARTPTS"
    )
    ending_filter = normalize
    if final_subtitle is not None:
        final_subtitle_filter = escape_filter_path(final_subtitle)
        ending_filter += f",ass=filename='{final_subtitle_filter}'"
    filter_graph = (
        f"[0:v]{normalize},ass=filename='{subtitle_filter}'[captioned];"
        f"[1:v]{ending_filter}[ending];"
        "[captioned][ending]concat=n=2:v=1:a=0,format=yuv420p[outv]"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(first),
        "-i",
        str(final),
    ]
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music)])
        filter_graph += (
            f";[2:a]volume={music_volume:.4f},"
            "aresample=48000,asetpts=PTS-STARTPTS[aout]"
        )
    command.extend([
        "-filter_complex",
        filter_graph,
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ])
    if music is None:
        # Explicitly discard every audio stream from both video inputs.
        command.append("-an")
    else:
        command.extend(
            ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        )
    command.append(str(output))
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise VideoMakerError(f"FFmpeg failed with exit code {exc.returncode}") from exc


def run(args: argparse.Namespace) -> tuple[int, str, Path]:
    final_path = resolve_final_video(args.final_directory, args.final)
    return generate_video(
        language=args.language,
        index=args.index,
        position=args.position,
        carousel=args.carousel,
        carousel_position=args.carousel_position,
        music_path=args.music,
        music_volume=args.music_volume,
        first_path=args.first,
        final_path=final_path,
        data_path=args.data,
        output_path=args.output,
        overwrite=args.overwrite,
    )


def generate_video(
    *,
    language: str = "en",
    index: int | None = None,
    position: str = "top",
    carousel: bool = False,
    carousel_position: str = "top",
    music_path: Path | None = DEFAULT_MUSIC,
    music_volume: float = 1.0,
    first_path: Path = DEFAULT_FIRST,
    final_path: Path,
    data_path: Path = DEFAULT_DATA,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> tuple[int, str, Path]:
    """Generate a video for CLI or API callers and return caption metadata."""
    if language not in LANGUAGES:
        raise VideoMakerError(f"Unsupported language: {language}")
    if position not in POSITIONS:
        raise VideoMakerError(f"Unsupported caption position: {position}")
    if carousel_position not in POSITIONS:
        raise VideoMakerError(
            f"Unsupported carousel caption position: {carousel_position}"
        )
    if not 0 <= music_volume <= 1:
        raise VideoMakerError("Music volume must be between 0 and 1")

    ffmpeg = ensure_program("ffmpeg")
    ffprobe = ensure_program("ffprobe")
    first = require_file(first_path, "First video")
    final = require_file(final_path, "Final video")
    data = require_file(data_path, "Caption data")
    music = require_file(music_path, "Background music") if music_path else None
    automatic_output = output_path is None
    selected_output = default_output_path(first) if automatic_output else output_path
    output = selected_output.expanduser().resolve()

    if output in {first, final, data}:
        raise VideoMakerError("Output must not overwrite an input or data file")
    if output.exists() and not overwrite:
        raise VideoMakerError(
            f"Output already exists: {output} (use --overwrite to replace it)"
        )
    if automatic_output:
        output.parent.mkdir(parents=True, exist_ok=True)
    elif not output.parent.is_dir():
        raise VideoMakerError(f"Output directory does not exist: {output.parent}")

    captions = load_captions(data, language)
    selected_index, caption = select_caption(captions, index)
    first_duration = probe_duration(ffprobe, first)
    carousel_caption = load_carousel_caption(data, language) if carousel else None
    final_duration = probe_duration(ffprobe, final) if carousel else None

    with tempfile.TemporaryDirectory(prefix="viral-maker-") as temp_dir:
        subtitle = Path(temp_dir) / "caption.ass"
        subtitle.write_text(
            create_ass(caption, first_duration, position), encoding="utf-8"
        )
        final_subtitle = None
        if carousel_caption is not None and final_duration is not None:
            final_subtitle = Path(temp_dir) / "carousel.ass"
            final_subtitle.write_text(
                create_ass(carousel_caption, final_duration, carousel_position),
                encoding="utf-8",
            )
        make_video(
            ffmpeg,
            first,
            final,
            subtitle,
            output,
            overwrite,
            final_subtitle,
            music,
            music_volume,
        )

    return selected_index, caption, output


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.music = resolve_cli_music(args)
        selected_index, caption, output = run(args)
    except (AudioDownloadError, VideoMakerError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Caption #{selected_index} ({args.language}): {caption}")
    if args.carousel:
        print(f"Carousel prompt: enabled ({args.carousel_position})")
    print(f"Created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
