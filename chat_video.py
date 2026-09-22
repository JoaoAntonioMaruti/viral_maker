"""Record director-driven chat conversations with Playwright and FFmpeg."""

from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import chat_video_jobs


CHAT_START_TIMEOUT_MS = 60_000
CHAT_FINISH_TIMEOUT_MS = 300_000
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 60
CAPTURE_WIDTH = 540
CAPTURE_HEIGHT = 960
STOP_POLL_INTERVAL_MS = 100
MINIMUM_RECORDING_MS = 250
START_TRIM_MS = 100


class ChatVideoError(RuntimeError):
    """An expected, user-facing chat video recording error."""


@dataclass(frozen=True)
class ChatVideoResult:
    path: Path
    execution_id: str
    conversation_id: str
    duration_ms: int
    stopped_early: bool = False


def encode_schema(schema_json: str) -> str:
    return urlsafe_b64encode(schema_json.encode("utf-8")).decode("ascii")


def build_director_url(client_url: str, schema_json: str) -> str:
    base_url = client_url.rstrip("/")
    return f"{base_url}?director=chat&schema64={encode_schema(schema_json)}"


def _sanitize_job_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return re.sub(
        r"(schema64=)[A-Za-z0-9_=-]+",
        r"\1[redacted]",
        message,
    )


def _require_program(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ChatVideoError(f"Required program was not found: {name}")
    return executable


def _load_audio_sink(pactl: str, sink_name: str) -> str:
    command = [
        pactl,
        "load-module",
        "module-null-sink",
        f"sink_name={sink_name}",
        "rate=48000",
        "channels=2",
        "sink_properties=device.description=viral-maker-chat-video",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ChatVideoError(f"Could not create browser audio sink: {detail}")
    return result.stdout.strip()


def _unload_audio_sink(pactl: str, module_id: str) -> None:
    subprocess.run(
        [pactl, "unload-module", module_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_audio_capture(
    ffmpeg: str, sink_name: str, output: Path
) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "pulse",
            "-i",
            f"{sink_name}.monitor",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.2)
    if process.poll() is not None:
        _, stderr = process.communicate()
        raise ChatVideoError(
            "Could not start browser audio capture: "
            + ((stderr or "FFmpeg exited unexpectedly").strip())
        )
    return process


def _stop_audio_capture(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _event_duration_ms(started: dict[str, Any], finished: dict[str, Any]) -> int:
    duration = finished.get("durationMs")
    if isinstance(duration, (int, float)) and duration > 0:
        return round(duration)
    observed_duration = finished.get("observedAt", 0) - started.get("observedAt", 0)
    if not isinstance(observed_duration, (int, float)) or observed_duration <= 0:
        raise ChatVideoError("The director returned an invalid recording duration")
    return round(observed_duration)


def _recording_duration_ms(
    play_requested_at: float,
    started: dict[str, Any],
    finished: dict[str, Any],
) -> int:
    observed_finish = finished.get("observedAt")
    if isinstance(observed_finish, (int, float)) and observed_finish > play_requested_at:
        return round(observed_finish - play_requested_at)
    return _event_duration_ms(started, finished)


def _estimate_recording_duration_ms(schema: dict[str, Any]) -> int:
    def positive_number(value: Any) -> float:
        return float(value) if isinstance(value, (int, float)) and value > 0 else 0

    total = 0.0
    for event in schema.get("events", []):
        if not isinstance(event, dict):
            continue
        total += positive_number(event.get("delayMs"))
        reveal = event.get("reveal") or {}
        if isinstance(reveal, dict):
            total += positive_number(reveal.get("loadingMs"))
            characters_per_second = reveal.get("charactersPerSecond")
            message = event.get("message")
            if (
                isinstance(characters_per_second, (int, float))
                and characters_per_second > 0
                and isinstance(message, str)
            ):
                total += len(message) / characters_per_second * 1000
        audio = event.get("audio") or {}
        if isinstance(audio, dict) and isinstance(audio.get("text"), str):
            total += len(audio["text"]) / 12 * 1000
        total += positive_number(event.get("holdMs"))
    return max(1000, round(total))


def _mux_recording(
    ffmpeg: str,
    raw_video: Path,
    raw_audio: Path,
    output: Path,
    *,
    video_offset_ms: float,
    audio_offset_ms: float,
    duration_ms: int,
) -> None:
    duration = duration_ms / 1000
    video_offset = max(0, video_offset_ms / 1000)
    audio_offset = max(0, audio_offset_ms / 1000)
    filter_graph = (
        f"[0:v]trim=start={video_offset:.6f},setpts=PTS-STARTPTS,"
        "tpad=stop_mode=clone:stop_duration=5,"
        f"trim=duration={duration:.6f},"
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},setsar=1[v];"
        f"[1:a]atrim=start={audio_offset:.6f},asetpts=PTS-STARTPTS,"
        f"aresample=48000,apad,atrim=duration={duration:.6f}[a]"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(raw_video),
        "-i",
        str(raw_audio),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-t",
        f"{duration:.6f}",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ChatVideoError(
            "Could not compose chat video: "
            + (result.stderr.strip() or f"FFmpeg exited with {result.returncode}")
        )


def _validate_output(ffprobe: str, output: Path) -> None:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ChatVideoError("Could not validate the generated chat video")
    try:
        streams = json.loads(result.stdout)["streams"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ChatVideoError("FFprobe returned invalid chat video metadata") from exc
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if (
        video is None
        or video.get("codec_name") != "h264"
        or video.get("width") != VIDEO_WIDTH
        or video.get("height") != VIDEO_HEIGHT
        or audio is None
        or audio.get("codec_name") != "aac"
    ):
        raise ChatVideoError("Generated chat video has unexpected media streams")


def _load_sync_playwright() -> Callable:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ChatVideoError(
            "Playwright is not installed. Run 'make setup' first."
        ) from exc
    return sync_playwright


def capture_chat_video(
    schema_json: str,
    output: Path,
    *,
    injection_script: Path,
    client_url: str = "http://localhost:3000/play",
    headed: bool = False,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> ChatVideoResult:
    """Record one conversation and return its director metadata."""
    try:
        schema = json.loads(schema_json)
        npc_id = schema["npc"]["id"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ChatVideoError("Chat video schema has no valid npc.id") from exc
    if not isinstance(npc_id, str) or not npc_id.strip():
        raise ChatVideoError("Chat video schema has no valid npc.id")
    estimated_duration_ms = _estimate_recording_duration_ms(schema)

    def report_progress(progress: int, stage: str) -> None:
        if on_progress is not None:
            on_progress(progress, stage)

    report_progress(8, "preparing")

    ffmpeg = _require_program("ffmpeg")
    ffprobe = _require_program("ffprobe")
    pactl = _require_program("pactl")
    try:
        javascript = injection_script.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChatVideoError(
            f"Could not read injection script: {injection_script}"
        ) from exc

    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    sink_name = (
        "viral_maker_"
        + destination.stem.replace("-", "_")
        + f"_{os.getpid()}_{time.monotonic_ns():x}"
    )
    module_id: str | None = None
    audio_process: subprocess.Popen | None = None
    browser = None
    context = None
    screencast_started = False
    sync_playwright = _load_sync_playwright()

    with tempfile.TemporaryDirectory(prefix="viral-maker-chat-video-") as temp_dir:
        raw_video = Path(temp_dir) / "browser.webm"
        raw_audio = Path(temp_dir) / "browser.wav"
        audio_started_at = 0.0
        video_started_at = 0.0
        started: dict[str, Any] | None = None
        finished: dict[str, Any] | None = None
        stopped_at: float | None = None
        try:
            module_id = _load_audio_sink(pactl, sink_name)
            with sync_playwright() as playwright:
                browser_environment = dict(os.environ)
                browser_environment["PULSE_SINK"] = sink_name
                browser = playwright.chromium.launch(
                    headless=not headed,
                    args=["--autoplay-policy=no-user-gesture-required"],
                    ignore_default_args=["--mute-audio"],
                    env=browser_environment,
                )
                report_progress(12, "opening-browser")
                context = browser.new_context(
                    viewport={"width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT},
                    screen={"width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT},
                    device_scale_factor=1,
                )
                context.add_init_script(script=javascript)
                page = context.new_page()
                page.goto(
                    build_director_url(client_url, schema_json),
                    wait_until="load",
                    timeout=CHAT_START_TIMEOUT_MS,
                )
                report_progress(20, "loading-page")
                page.evaluate(
                    "config => { window.__chatVideoConfig = config; }",
                    {"npcId": npc_id},
                )
                page.evaluate("window.__prepareChatVideo()")
                report_progress(30, "dialog-ready")

                audio_started_at = time.time() * 1000
                audio_process = _start_audio_capture(ffmpeg, sink_name, raw_audio)
                video_started_at = time.time() * 1000
                page.screencast.start(
                    path=raw_video,
                    size={"width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT},
                )
                screencast_started = True
                play_requested_at = page.evaluate("window.__playChatVideo()")
                if not isinstance(play_requested_at, (int, float)):
                    raise ChatVideoError("Could not mark chat video start time")

                page.wait_for_function(
                    "window.__chatVideoCapture?.started?.length > 0",
                    timeout=CHAT_START_TIMEOUT_MS,
                )
                started = page.evaluate("window.__chatVideoCapture.started[0]")
                execution_id = started.get("executionId")
                if not isinstance(execution_id, str) or not execution_id:
                    raise ChatVideoError("Chat started event has no executionId")
                report_progress(40, "recording")
                finish_deadline = time.monotonic() + CHAT_FINISH_TIMEOUT_MS / 1000
                last_reported_progress = 40
                while time.monotonic() < finish_deadline:
                    finished = page.evaluate(
                        "executionId => window.__chatVideoCapture.finished.find("
                        "event => event.executionId === executionId) || null",
                        execution_id,
                    )
                    if finished is not None:
                        break
                    elapsed_ms = time.time() * 1000 - play_requested_at
                    if (
                        should_stop is not None
                        and elapsed_ms >= MINIMUM_RECORDING_MS
                        and should_stop()
                    ):
                        stopped_at = time.time() * 1000
                        report_progress(max(last_reported_progress, 85), "stopping")
                        break
                    estimated_progress = min(
                        85,
                        40 + round(45 * elapsed_ms / estimated_duration_ms),
                    )
                    if estimated_progress > last_reported_progress:
                        last_reported_progress = estimated_progress
                        report_progress(estimated_progress, "recording")
                    page.wait_for_timeout(STOP_POLL_INTERVAL_MS)
                else:
                    raise ChatVideoError(
                        "gameplayDirectorChatFinished was not emitted after "
                        f"{CHAT_FINISH_TIMEOUT_MS}ms"
                    )
                page.screencast.stop()
                report_progress(88, "finalizing")
                screencast_started = False
                _stop_audio_capture(audio_process)
                audio_process = None
                context.close()
                context = None
                browser.close()
                browser = None
        except ChatVideoError:
            raise
        except Exception as exc:
            raise ChatVideoError(f"Could not record chat video: {exc}") from exc
        finally:
            if screencast_started and context is not None:
                try:
                    page.screencast.stop()
                except Exception:
                    pass
            _stop_audio_capture(audio_process)
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if module_id is not None:
                _unload_audio_sink(pactl, module_id)

        assert started is not None
        stopped_early = stopped_at is not None
        if not stopped_early:
            assert finished is not None
            status = finished.get("status")
            if status != "completed":
                detail = finished.get("error") or f"director status was {status!r}"
                raise ChatVideoError(f"Chat director did not complete: {detail}")
        observed_start = started.get("observedAt")
        if not isinstance(observed_start, (int, float)):
            raise ChatVideoError("Chat started event has no valid observation time")
        captured_duration_ms = (
            round(stopped_at - play_requested_at)
            if stopped_at is not None
            else _recording_duration_ms(play_requested_at, started, finished)
        )
        duration_ms = captured_duration_ms - START_TRIM_MS
        if duration_ms <= 0:
            raise ChatVideoError("Chat video is too short after start trimming")
        _mux_recording(
            ffmpeg,
            raw_video,
            raw_audio,
            destination,
            video_offset_ms=(
                play_requested_at - video_started_at + START_TRIM_MS
            ),
            audio_offset_ms=(
                play_requested_at - audio_started_at + START_TRIM_MS
            ),
            duration_ms=duration_ms,
        )
        report_progress(97, "validating")
        try:
            _validate_output(ffprobe, destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    return ChatVideoResult(
        path=destination,
        execution_id=str(started["executionId"]),
        conversation_id=str(started.get("conversationId") or schema.get("id") or ""),
        duration_ms=duration_ms,
        stopped_early=stopped_early,
    )


class ChatVideoWorker:
    """Single background worker backed by the persistent SQLite queue."""

    def __init__(
        self,
        database_path: Path,
        output_directory: Path,
        injection_script: Path,
        render_lock: threading.Lock,
        *,
        poll_interval: float = 0.5,
        capture: Callable[..., ChatVideoResult] = capture_chat_video,
    ) -> None:
        self.database_path = database_path
        self.output_directory = output_directory
        self.injection_script = injection_script
        self.render_lock = render_lock
        self.poll_interval = poll_interval
        self.capture = capture
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        chat_video_jobs.initialize(self.database_path)
        chat_video_jobs.recover_interrupted(self.database_path)
        self._thread = threading.Thread(
            target=self._run,
            name="chat-video-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            job = chat_video_jobs.claim_next(self.database_path)
            if job is None:
                self._stop_event.wait(self.poll_interval)
                continue
            output = self.output_directory / f"{job['id']}.mp4"
            try:
                with self.render_lock:
                    result = self.capture(
                        job["schema_json"],
                        output,
                        injection_script=self.injection_script,
                        headed=bool(job["headed"]),
                        should_stop=lambda: chat_video_jobs.is_stop_requested(
                            self.database_path, job["id"]
                        ),
                        on_progress=lambda progress, stage: (
                            chat_video_jobs.update_progress(
                                self.database_path,
                                job["id"],
                                progress,
                                stage,
                            )
                        ),
                    )
                chat_video_jobs.complete(
                    self.database_path,
                    job["id"],
                    output_filename=result.path.name,
                    execution_id=result.execution_id,
                    conversation_id=result.conversation_id,
                    duration_ms=result.duration_ms,
                    stopped_early=result.stopped_early,
                )
            except Exception as exc:
                output.unlink(missing_ok=True)
                chat_video_jobs.fail(
                    self.database_path,
                    job["id"],
                    _sanitize_job_error(exc),
                )
