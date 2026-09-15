"""HTTP API for generating and downloading social videos."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from video_maker import (
    VideoMakerError,
    default_output_path,
    generate_video,
    list_final_videos,
    resolve_final_video,
)


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIRECTORY = PROJECT_ROOT / "outputs"
FIRST_VIDEO = PROJECT_ROOT / "videos" / "1.mp4"
FINAL_VIDEO_DIRECTORY = PROJECT_ROOT / "videos" / "final_videos"
CAPTION_DATA = PROJECT_ROOT / "data.json"
MUSIC_FILE = PROJECT_ROOT / "audio" / "ssstik.io_1789458727141.mp3"

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
    final: int = Field(default=1, ge=1)
    carousel: bool = False
    carousel_position: Literal["top", "center", "bottom"] = "top"
    music: bool = True
    music_volume: float = Field(default=1.0, ge=0, le=1)


class VideoResult(BaseModel):
    id: str
    status: Literal["completed"]
    language: Literal["en", "pt", "ja"]
    caption_index: int
    caption: str
    position: Literal["top", "center", "bottom"]
    final: int
    carousel: bool
    carousel_position: Literal["top", "center", "bottom"]
    music: bool
    music_volume: float
    download_url: str


class VideoStatus(BaseModel):
    id: str
    status: Literal["completed"]
    download_url: str


class FinalVideo(BaseModel):
    number: int
    filename: str


def video_path(video_id: str) -> Path:
    if not video_id or Path(video_id).name != video_id:
        raise HTTPException(status_code=404, detail="Video not found")
    candidate = (OUTPUT_DIRECTORY / f"{video_id}.mp4").resolve()
    if candidate.parent != OUTPUT_DIRECTORY.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return candidate


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/finals", response_model=list[FinalVideo])
def get_final_videos() -> list[FinalVideo]:
    try:
        return [
            FinalVideo(number=number, filename=path.name)
            for number, path in list_final_videos(FINAL_VIDEO_DIRECTORY)
        ]
    except VideoMakerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/videos/reaction",
    response_model=VideoResult,
    status_code=status.HTTP_201_CREATED,
)
def create_video(payload: VideoCreate, request: Request) -> VideoResult:
    try:
        with render_lock:
            final_video = resolve_final_video(FINAL_VIDEO_DIRECTORY, payload.final)
            OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
            output = OUTPUT_DIRECTORY / default_output_path(FIRST_VIDEO).name
            selected_index, caption, created_path = generate_video(
                language=payload.language,
                index=payload.index,
                position=payload.position,
                carousel=payload.carousel,
                carousel_position=payload.carousel_position,
                music_path=MUSIC_FILE if payload.music else None,
                music_volume=payload.music_volume,
                first_path=FIRST_VIDEO,
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
        final=payload.final,
        carousel=payload.carousel,
        carousel_position=payload.carousel_position,
        music=payload.music,
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
