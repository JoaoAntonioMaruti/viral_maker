"""HTTP API for generating and downloading social videos."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from video_maker import (
    VideoMakerError,
    default_output_path,
    generate_video,
    list_final_videos,
    load_captions,
    load_carousel_caption,
    resolve_final_video,
)


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs"
VIDEO_DIRECTORY = PROJECT_ROOT / "videos"
FINAL_VIDEO_DIRECTORY = PROJECT_ROOT / "videos" / "final_videos"
CAPTION_DATA = PROJECT_ROOT / "data.json"
AUDIO_DIRECTORY = PROJECT_ROOT / "audio"
MUSIC_FILE = AUDIO_DIRECTORY / "ssstik.io_1789458727141.mp3"
SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}

# Video encoding is CPU-heavy. A single worker processes one video at a time.
render_lock = Lock()

app = FastAPI(
    title="Viral Maker API",
    description="Generate captioned vertical videos with FFmpeg.",
    version="1.0.0",
)


class VideoCreate(BaseModel):
    language: Literal["en", "pt", "ja"] = "en"
    index: int | None = Field(default=None, ge=1)
    position: Literal["top", "center", "bottom"] = "top"
    video: int = Field(default=1, ge=1)
    final: int = Field(default=1, ge=1)
    carousel: bool = False
    carousel_position: Literal["top", "center", "bottom"] = "top"
    music: bool = True
    music_filename: str | None = None
    music_volume: float = Field(default=1.0, ge=0, le=1)


class VideoResult(BaseModel):
    id: str
    status: Literal["completed"]
    language: Literal["en", "pt", "ja"]
    caption_index: int
    caption: str
    position: Literal["top", "center", "bottom"]
    video: int
    final: int
    carousel: bool
    carousel_position: Literal["top", "center", "bottom"]
    music: bool
    music_filename: str | None
    music_volume: float
    download_url: str


class VideoStatus(BaseModel):
    id: str
    status: Literal["completed"]
    download_url: str


class FinalVideo(BaseModel):
    number: int
    filename: str
    url: str


class InitialVideo(BaseModel):
    number: int
    filename: str
    size_bytes: int
    url: str


class AudioFile(BaseModel):
    filename: str
    size_bytes: int
    is_default: bool
    url: str


class CaptionData(BaseModel):
    language: Literal["en", "pt", "ja"]
    carousel: str
    data: list[str]


class OutputFile(BaseModel):
    id: str
    filename: str
    size_bytes: int
    created_at: datetime
    url: str
    download_url: str


def video_path(video_id: str) -> Path:
    if not video_id or Path(video_id).name != video_id:
        raise HTTPException(status_code=404, detail="Video not found")
    candidate = (OUTPUT_DIRECTORY / f"{video_id}.mp4").resolve()
    if candidate.parent != OUTPUT_DIRECTORY.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return candidate


def resolve_audio_file(filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise VideoMakerError("Invalid music filename")
    if Path(filename).suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise VideoMakerError(f"Unsupported audio file: {filename}")
    candidate = (AUDIO_DIRECTORY / filename).resolve()
    if candidate.parent != AUDIO_DIRECTORY.resolve() or not candidate.is_file():
        raise VideoMakerError(f"Audio file not found: {filename}")
    return candidate


def resolve_initial_video(number: int) -> Path:
    if number < 1:
        raise VideoMakerError("Initial video number must be 1 or greater")
    candidate = (VIDEO_DIRECTORY / f"{number}.mp4").resolve()
    if candidate.parent != VIDEO_DIRECTORY.resolve() or not candidate.is_file():
        available = [str(video.number) for video in get_initial_videos()]
        choices = ", ".join(available) or "none"
        raise VideoMakerError(
            f"Initial video {number} not found. Available: {choices}"
        )
    return candidate


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/finals", response_model=list[FinalVideo])
def get_final_videos() -> list[FinalVideo]:
    try:
        return [
            FinalVideo(
                number=number,
                filename=path.name,
                url=f"/media/videos/final_videos/{path.name}",
            )
            for number, path in list_final_videos(FINAL_VIDEO_DIRECTORY)
        ]
    except VideoMakerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/videos", response_model=list[InitialVideo])
def get_initial_videos() -> list[InitialVideo]:
    if not VIDEO_DIRECTORY.is_dir():
        return []
    videos = [
        (int(path.stem), path)
        for path in VIDEO_DIRECTORY.glob("*.mp4")
        if path.is_file() and path.stem.isdigit() and int(path.stem) >= 1
    ]
    videos.sort(key=lambda item: item[0])
    return [
        InitialVideo(
            number=number,
            filename=path.name,
            size_bytes=path.stat().st_size,
            url=f"/media/videos/{path.name}",
        )
        for number, path in videos
    ]


@app.get("/audios", response_model=list[AudioFile])
def get_audio_files() -> list[AudioFile]:
    if not AUDIO_DIRECTORY.is_dir():
        return []
    default_music = MUSIC_FILE.resolve()
    files = [
        path
        for path in AUDIO_DIRECTORY.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    files.sort(key=lambda path: path.name.casefold())
    return [
        AudioFile(
            filename=path.name,
            size_bytes=path.stat().st_size,
            is_default=path.resolve() == default_music,
            url=f"/media/audios/{path.name}",
        )
        for path in files
    ]


@app.get("/data", response_model=CaptionData)
def get_caption_data(language: Literal["en", "pt", "ja"]) -> CaptionData:
    try:
        return CaptionData(
            language=language,
            carousel=load_carousel_caption(CAPTION_DATA, language),
            data=load_captions(CAPTION_DATA, language),
        )
    except VideoMakerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/outputs", response_model=list[OutputFile])
def get_output_files() -> list[OutputFile]:
    if not OUTPUT_DIRECTORY.is_dir():
        return []
    files = [
        path
        for path in OUTPUT_DIRECTORY.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4"
    ]
    files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return [
        OutputFile(
            id=path.stem,
            filename=path.name,
            size_bytes=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
            url=f"/media/outputs/{path.name}",
            download_url=f"/videos/{path.stem}/download",
        )
        for path in files
    ]


@app.post(
    "/videos/reaction",
    response_model=VideoResult,
    status_code=status.HTTP_201_CREATED,
)
def create_video(payload: VideoCreate, request: Request) -> VideoResult:
    selected_music: Path | None = None
    try:
        with render_lock:
            initial_video = resolve_initial_video(payload.video)
            final_video = resolve_final_video(FINAL_VIDEO_DIRECTORY, payload.final)
            if payload.music:
                selected_music = resolve_audio_file(
                    payload.music_filename or MUSIC_FILE.name
                )
            OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
            output = OUTPUT_DIRECTORY / default_output_path(initial_video).name
            selected_index, caption, created_path = generate_video(
                language=payload.language,
                index=payload.index,
                position=payload.position,
                carousel=payload.carousel,
                carousel_position=payload.carousel_position,
                music_path=selected_music,
                music_volume=payload.music_volume,
                first_path=initial_video,
                final_path=final_video,
                data_path=CAPTION_DATA,
                output_path=output,
            )
    except VideoMakerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    video_id = created_path.stem
    return VideoResult(
        id=video_id,
        status="completed",
        language=payload.language,
        caption_index=selected_index,
        caption=caption,
        position=payload.position,
        video=payload.video,
        final=payload.final,
        carousel=payload.carousel,
        carousel_position=payload.carousel_position,
        music=payload.music,
        music_filename=selected_music.name if selected_music else None,
        music_volume=payload.music_volume,
        download_url=str(request.url_for("download_video", video_id=video_id)),
    )


@app.get("/videos/{video_id}", response_model=VideoStatus)
def get_video_status(video_id: str, request: Request) -> VideoStatus:
    path = video_path(video_id)
    return VideoStatus(
        id=path.stem,
        status="completed",
        download_url=str(request.url_for("download_video", video_id=path.stem)),
    )


@app.get("/videos/{video_id}/download", name="download_video")
def download_video(video_id: str) -> FileResponse:
    path = video_path(video_id)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


# Static media mounts are declared after API routes so they cannot shadow them.
app.mount(
    "/media/audios",
    StaticFiles(directory=AUDIO_DIRECTORY, check_dir=False),
    name="audio-media",
)
app.mount(
    "/media/videos",
    StaticFiles(directory=PROJECT_ROOT / "videos", check_dir=False),
    name="video-media",
)
app.mount(
    "/media/outputs",
    StaticFiles(directory=OUTPUT_DIRECTORY, check_dir=False),
    name="output-media",
)
