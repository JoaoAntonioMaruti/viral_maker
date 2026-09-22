"""HTTP API for generating and downloading social videos."""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import chat_video_jobs
from audio_metrics import fetch_all_metrics, upsert_metrics
from chat_video import ChatVideoWorker
from download_tiktok_audio import AudioDownloadError, download_audio_with_metadata
from generation_assets import (
    delete_video_records,
    fetch_all_by_video_id,
    fetch_all_generations_by_video_id,
    fetch_by_video_id,
    fetch_generation_by_video_id,
    fetch_screenshot_history,
    save_video_generation,
    save_video_screenshot,
)
from screenshot import ScreenshotError, capture_screenshot, read_javascript
from video_maker import (
    VideoMakerError,
    default_output_path,
    generate_video,
    list_mp4_videos_by_creation,
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
DATABASE_PATH = PROJECT_ROOT / "audio_metrics.db"
GENERATION_DATABASE_PATH = PROJECT_ROOT / "generation_assets.db"
SCREENSHOT_SCRIPT = PROJECT_ROOT / "assets" / "inject.js"
CHAT_VIDEO_SCRIPT = PROJECT_ROOT / "assets" / "chat_video_inject.js"
CHAT_VIDEO_DATABASE_PATH = PROJECT_ROOT / "chat_video_jobs.db"
DEFAULT_SCREENSHOT_CLIENT_URL = "http://127.0.0.1:3000/play"
SUPPORTED_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}

# Video encoding is CPU-heavy. A single worker processes one video at a time.
render_lock = Lock()
chat_video_worker: ChatVideoWorker | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global chat_video_worker
    worker = ChatVideoWorker(
        CHAT_VIDEO_DATABASE_PATH,
        OUTPUT_DIRECTORY,
        CHAT_VIDEO_SCRIPT,
        render_lock,
    )
    chat_video_worker = worker
    worker.start()
    try:
        yield
    finally:
        worker.stop()
        chat_video_worker = None


app = FastAPI(
    title="Viral Maker API",
    description="Generate captioned vertical videos with FFmpeg.",
    version="1.0.0",
    lifespan=lifespan,
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
    client_url: str = DEFAULT_SCREENSHOT_CLIENT_URL
    screenshot_width: int = Field(default=540, ge=1, le=7680)
    screenshot_height: int = Field(default=960, ge=1, le=7680)
    npc_id: str | None = None
    clothes: str | None = None
    npc_name: str | None = None
    description: str | None = None
    message: str | None = None
    actions: list[str] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_screenshot_fields_for_carousel(self) -> "VideoCreate":
        if not self.carousel:
            return self
        required = {
            "npc_id": self.npc_id,
            "clothes": self.clothes,
            "npc_name": self.npc_name,
            "description": self.description,
            "message": self.message,
        }
        missing = [
            name for name, value in required.items() if not value or not value.strip()
        ]
        if (
            self.actions is None
            or not self.actions
            or any(not item.strip() for item in self.actions)
        ):
            missing.append("actions")
        if missing:
            raise ValueError(
                "carousel requires non-empty screenshot fields: "
                + ", ".join(missing)
            )
        return self


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
    screenshot_id: str | None = None
    screenshot_url: str | None = None


class VideoGenerationMetadata(BaseModel):
    video_id: str
    initial_video: int
    initial_video_filename: str
    final_video: int
    final_video_filename: str
    language: Literal["en", "pt", "ja"]
    caption_index: int
    caption: str
    position: Literal["top", "center", "bottom"]
    carousel: bool
    carousel_position: Literal["top", "center", "bottom"]
    music: bool
    music_filename: str | None
    music_volume: float
    created_at: datetime


class VideoStatus(BaseModel):
    id: str
    status: Literal["completed"]
    download_url: str
    screenshot_id: str | None = None
    screenshot_url: str | None = None
    generation: VideoGenerationMetadata | None = None


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
    url: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


class AudioCreate(BaseModel):
    url: str
    overwrite: bool = False
    cookies_from_browser: str | None = None


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
    screenshot_id: str | None = None
    screenshot_url: str | None = None
    generation: VideoGenerationMetadata | None = None


class ScreenshotMockHistory(BaseModel):
    video_id: str
    screenshot_id: str
    npc_id: str
    clothes: str
    client_url: str
    width: int
    height: int
    npc_name: str
    description: str
    message: str
    actions: list[str]
    language: Literal["en", "pt", "ja"] | None
    created_at: datetime
    video_url: str
    screenshot_url: str


class ChatVideoNpc(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    renderer: dict[str, Any]


class ChatVideoCreate(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(min_length=1)
    npc: ChatVideoNpc
    options: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("events")
    @classmethod
    def require_event_types(
        cls, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if any(
            not isinstance(event.get("type"), str) or not event["type"].strip()
            for event in events
        ):
            raise ValueError("every event must have a non-empty type")
        return events


class ChatVideoJob(BaseModel):
    id: str
    status: Literal["queued", "processing", "completed", "failed"]
    headed: bool
    stop_requested: bool
    stopped_early: bool
    status_url: str
    download_url: str | None
    execution_id: str | None = None
    conversation_id: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


def video_path(video_id: str) -> Path:
    if not video_id or Path(video_id).name != video_id:
        raise HTTPException(status_code=404, detail="Video not found")
    candidate = (OUTPUT_DIRECTORY / f"{video_id}.mp4").resolve()
    if candidate.parent != OUTPUT_DIRECTORY.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Video not found")
    return candidate


def screenshot_path(screenshot_id: str) -> Path:
    if not screenshot_id or Path(screenshot_id).name != screenshot_id:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    candidate = (OUTPUT_DIRECTORY / f"{screenshot_id}.png").resolve()
    if candidate.parent != OUTPUT_DIRECTORY.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return candidate


def _chat_video_job_response(
    job: dict[str, Any], request: Request
) -> ChatVideoJob:
    job_id = job["id"]
    completed = job["status"] == "completed"
    return ChatVideoJob(
        id=job_id,
        status=job["status"],
        headed=bool(job["headed"]),
        stop_requested=bool(job["stop_requested"]),
        stopped_early=bool(job["stopped_early"]),
        status_url=str(request.url_for("get_chat_video_job", job_id=job_id)),
        download_url=(
            str(request.url_for("download_chat_video", job_id=job_id))
            if completed
            else None
        ),
        execution_id=job["execution_id"],
        conversation_id=job["conversation_id"],
        duration_ms=job["duration_ms"],
        error=job["error"],
        created_at=job["created_at"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
    )


def _chat_video_output_path(job: dict[str, Any]) -> Path:
    filename = job.get("output_filename")
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Chat video not found")
    candidate = (OUTPUT_DIRECTORY / filename).resolve()
    if candidate.parent != OUTPUT_DIRECTORY.resolve() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Chat video not found")
    return candidate


def _remove_generated_files(*paths: Path | None) -> None:
    for path in paths:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def resolve_audio_file(filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise VideoMakerError("Invalid music filename")
    if Path(filename).suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise VideoMakerError(f"Unsupported audio file: {filename}")
    candidate = (AUDIO_DIRECTORY / filename).resolve()
    if candidate.parent != AUDIO_DIRECTORY.resolve() or not candidate.is_file():
        raise VideoMakerError(f"Audio file not found: {filename}")
    return candidate


def _audio_file_response(path: Path, metrics: dict[str, dict]) -> AudioFile:
    metric = metrics.get(path.stem)
    return AudioFile(
        filename=path.name,
        size_bytes=path.stat().st_size,
        url=f"/media/audios/{path.name}",
        views=metric.get("view_count") if metric else None,
        likes=metric.get("like_count") if metric else None,
        comments=metric.get("comment_count") if metric else None,
        shares=metric.get("share_count") if metric else None,
    )


def resolve_initial_video(number: int) -> Path:
    if number < 1:
        raise VideoMakerError("Initial video number must be 1 or greater")
    videos = list_mp4_videos_by_creation(VIDEO_DIRECTORY)
    if number > len(videos):
        choices = ", ".join(str(value) for value in range(1, len(videos) + 1))
        raise VideoMakerError(
            f"Initial video {number} not found. Available: {choices or 'none'}"
        )
    return videos[number - 1]


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
    return [
        InitialVideo(
            number=number,
            filename=path.name,
            size_bytes=path.stat().st_size,
            url=f"/media/videos/{path.name}",
        )
        for number, path in enumerate(
            list_mp4_videos_by_creation(VIDEO_DIRECTORY), start=1
        )
    ]


@app.get("/audios", response_model=list[AudioFile])
def get_audio_files() -> list[AudioFile]:
    if not AUDIO_DIRECTORY.is_dir():
        return []
    files = [
        path
        for path in AUDIO_DIRECTORY.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
    ]
    files.sort(key=lambda path: path.name.casefold())
    metrics = fetch_all_metrics(DATABASE_PATH)
    return [_audio_file_response(path, metrics) for path in files]


@app.post("/audios", response_model=AudioFile, status_code=status.HTTP_201_CREATED)
def create_audio(payload: AudioCreate) -> AudioFile:
    try:
        downloaded, metadata = download_audio_with_metadata(
            payload.url,
            AUDIO_DIRECTORY,
            overwrite=payload.overwrite,
            cookies_from_browser=payload.cookies_from_browser,
        )
    except AudioDownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if metadata:
        upsert_metrics(
            DATABASE_PATH,
            downloaded.stem,
            source_url=metadata.get("source_url") or payload.url,
            title=metadata.get("title"),
            author=metadata.get("author"),
            view_count=metadata.get("view_count"),
            like_count=metadata.get("like_count"),
            comment_count=metadata.get("comment_count"),
            share_count=metadata.get("share_count"),
        )

    metrics = fetch_all_metrics(DATABASE_PATH)
    return _audio_file_response(downloaded, metrics)


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
    screenshots = fetch_all_by_video_id(GENERATION_DATABASE_PATH)
    generations = fetch_all_generations_by_video_id(GENERATION_DATABASE_PATH)

    outputs = []
    for path in files:
        if not path.is_file():
            continue
        screenshot = screenshots.get(path.stem)
        screenshot_id = None
        if screenshot is not None:
            screenshot_id = screenshot["screenshot_id"]
            if not (OUTPUT_DIRECTORY / f"{screenshot_id}.png").is_file():
                continue
        outputs.append(
            OutputFile(
                id=path.stem,
                filename=path.name,
                size_bytes=path.stat().st_size,
                created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                url=f"/media/outputs/{path.name}",
                download_url=f"/videos/{path.stem}/download",
                screenshot_id=screenshot_id,
                screenshot_url=(
                    f"/screenshots/{screenshot_id}/download"
                    if screenshot_id is not None
                    else None
                ),
                generation=generations.get(path.stem),
            )
        )
    return outputs


@app.get("/screenshots/history", response_model=list[ScreenshotMockHistory])
def get_screenshot_history(
    language: Literal["en", "pt", "ja"] | None = None,
) -> list[ScreenshotMockHistory]:
    history = []
    for record in fetch_screenshot_history(GENERATION_DATABASE_PATH, language):
        video_file = OUTPUT_DIRECTORY / f"{record['video_id']}.mp4"
        screenshot_file = OUTPUT_DIRECTORY / f"{record['screenshot_id']}.png"
        if not video_file.is_file() or not screenshot_file.is_file():
            continue
        history.append(
            ScreenshotMockHistory(
                **record,
                video_url=f"/videos/{record['video_id']}/download",
                screenshot_url=f"/screenshots/{record['screenshot_id']}/download",
            )
        )
    return history


@app.post(
    "/videos/reaction",
    response_model=VideoResult,
    status_code=status.HTTP_201_CREATED,
)
def create_video(payload: VideoCreate, request: Request) -> VideoResult:
    selected_music: Path | None = None
    screenshot_id: str | None = None
    created_path: Path | None = None
    screenshot_output: Path | None = None
    try:
        with render_lock:
            initial_video = resolve_initial_video(payload.video)
            final_video = resolve_final_video(FINAL_VIDEO_DIRECTORY, payload.final)
            if payload.music and payload.music_filename:
                selected_music = resolve_audio_file(payload.music_filename)
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
            if payload.carousel:
                assert payload.npc_id is not None
                assert payload.clothes is not None
                assert payload.npc_name is not None
                assert payload.description is not None
                assert payload.message is not None
                assert payload.actions is not None
                screenshot_id = f"{created_path.stem}_screenshot"
                screenshot_output = OUTPUT_DIRECTORY / f"{screenshot_id}.png"
                created_screenshot = capture_screenshot(
                    payload.client_url,
                    payload.screenshot_width,
                    payload.screenshot_height,
                    screenshot_output,
                    npc_id=payload.npc_id,
                    clothes=payload.clothes,
                    mock_data={
                        "npcName": payload.npc_name,
                        "description": payload.description,
                        "message": payload.message,
                        "actions": payload.actions,
                    },
                    javascript=read_javascript(SCREENSHOT_SCRIPT),
                )
                save_video_screenshot(
                    GENERATION_DATABASE_PATH,
                    created_path.stem,
                    created_screenshot.stem,
                    npc_id=payload.npc_id,
                    clothes=payload.clothes,
                    client_url=payload.client_url,
                    width=payload.screenshot_width,
                    height=payload.screenshot_height,
                    npc_name=payload.npc_name,
                    description=payload.description,
                    message=payload.message,
                    actions=payload.actions,
                    language=payload.language,
                )
            save_video_generation(
                GENERATION_DATABASE_PATH,
                created_path.stem,
                initial_video=payload.video,
                initial_video_filename=initial_video.name,
                final_video=payload.final,
                final_video_filename=final_video.name,
                language=payload.language,
                caption_index=selected_index,
                caption=caption,
                position=payload.position,
                carousel=payload.carousel,
                carousel_position=payload.carousel_position,
                music=selected_music is not None,
                music_filename=selected_music.name if selected_music else None,
                music_volume=payload.music_volume,
            )
    except ScreenshotError as exc:
        _remove_generated_files(screenshot_output, created_path)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        _remove_generated_files(screenshot_output, created_path)
        if created_path is not None:
            try:
                delete_video_records(GENERATION_DATABASE_PATH, created_path.stem)
            except sqlite3.Error:
                pass
        raise HTTPException(
            status_code=500, detail="Could not save video generation metadata"
        ) from exc
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
        music=selected_music is not None,
        music_filename=selected_music.name if selected_music else None,
        music_volume=payload.music_volume,
        download_url=str(request.url_for("download_video", video_id=video_id)),
        screenshot_id=screenshot_id,
        screenshot_url=(
            str(request.url_for("download_screenshot", screenshot_id=screenshot_id))
            if screenshot_id
            else None
        ),
    )


@app.post(
    "/videos/chat-video",
    response_model=ChatVideoJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_chat_video(
    payload: ChatVideoCreate,
    request: Request,
    headed: bool = False,
) -> ChatVideoJob:
    job_id = f"chat-{uuid4().hex}"
    schema_json = json.dumps(
        payload.model_dump(by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        job = chat_video_jobs.create(
            CHAT_VIDEO_DATABASE_PATH,
            job_id,
            schema_json,
            headed=headed,
        )
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail="Could not enqueue chat video"
        ) from exc
    return _chat_video_job_response(job, request)


@app.get(
    "/videos/chat-video/{job_id}",
    response_model=ChatVideoJob,
    name="get_chat_video_job",
)
def get_chat_video_job(job_id: str, request: Request) -> ChatVideoJob:
    try:
        job = chat_video_jobs.fetch(CHAT_VIDEO_DATABASE_PATH, job_id)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail="Could not read chat video job"
        ) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Chat video job not found")
    return _chat_video_job_response(job, request)


@app.post(
    "/videos/chat-video/{job_id}/stop",
    response_model=ChatVideoJob,
    status_code=status.HTTP_202_ACCEPTED,
    name="stop_chat_video",
)
def stop_chat_video(job_id: str, request: Request) -> ChatVideoJob:
    try:
        job = chat_video_jobs.fetch(CHAT_VIDEO_DATABASE_PATH, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Chat video job not found")
        if job["status"] == "queued":
            raise HTTPException(
                status_code=409,
                detail="Chat video recording has not started",
            )
        if job["status"] == "failed":
            raise HTTPException(
                status_code=409,
                detail="Failed chat video cannot be stopped",
            )
        if job["status"] == "processing":
            job = chat_video_jobs.request_stop(CHAT_VIDEO_DATABASE_PATH, job_id)
            assert job is not None
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail="Could not stop chat video job"
        ) from exc
    return _chat_video_job_response(job, request)


@app.get(
    "/videos/chat-video/{job_id}/download",
    name="download_chat_video",
)
def download_chat_video(job_id: str) -> FileResponse:
    try:
        job = chat_video_jobs.fetch(CHAT_VIDEO_DATABASE_PATH, job_id)
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail="Could not read chat video job"
        ) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Chat video job not found")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Chat video is {job['status']}",
        )
    path = _chat_video_output_path(job)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/videos/{video_id}", response_model=VideoStatus)
def get_video_status(video_id: str, request: Request) -> VideoStatus:
    path = video_path(video_id)
    screenshot = fetch_by_video_id(GENERATION_DATABASE_PATH, path.stem)
    generation = fetch_generation_by_video_id(
        GENERATION_DATABASE_PATH, path.stem
    )
    screenshot_id = screenshot["screenshot_id"] if screenshot else None
    return VideoStatus(
        id=path.stem,
        status="completed",
        download_url=str(request.url_for("download_video", video_id=path.stem)),
        screenshot_id=screenshot_id,
        screenshot_url=(
            str(request.url_for("download_screenshot", screenshot_id=screenshot_id))
            if screenshot_id
            else None
        ),
        generation=generation,
    )


@app.get("/videos/{video_id}/download", name="download_video")
def download_video(video_id: str) -> FileResponse:
    path = video_path(video_id)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/screenshots/{screenshot_id}/download", name="download_screenshot")
def download_screenshot(screenshot_id: str) -> FileResponse:
    path = screenshot_path(screenshot_id)
    return FileResponse(path, media_type="image/png", filename=path.name)


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
