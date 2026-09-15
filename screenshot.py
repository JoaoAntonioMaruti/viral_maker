#!/usr/bin/env python3
"""Capture a web page viewport as a PNG using headless Chromium."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse


DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_OUTPUT_DIRECTORY = Path("outputs")
DEFAULT_MOCK_NPC_NAME = "ハナ"
DEFAULT_MOCK_DESCRIPTION = (
    "*ハナはバルコニーの扉のそばで、静かに雨音を聞いている。あなたが近づくと、"
    "速くなる鼓動を隠すように、胸元でそっと両手を重ねる。*"
)
DEFAULT_MOCK_MESSAGE = (
    "*頬を赤く染めながら、恥ずかしそうに視線を落とす* 私……こういうこと、まだ心の"
    "準備ができてないと思うの。考えただけで、胸がドキドキして……。*しばらく迷ったあと、"
    "勇気を振り絞ってあなたの手にそっと触れる* でも……あなたとなら、してみたい。"
    "だから……優しくしてね？♡"
)
DEFAULT_MOCK_ACTIONS = [
    "「焦らなくてもいいよ」と安心させる",
    "彼女の手を優しく握って近づく",
    "何をしてみたいのか尋ねる",
]


class ScreenshotError(RuntimeError):
    """An expected, user-facing screenshot error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a web page viewport as a PNG with headless Chromium."
    )
    parser.add_argument("url", help="HTTP or HTTPS page URL")
    parser.add_argument("--width", type=int, required=True, help="viewport width")
    parser.add_argument("--height", type=int, required=True, help="viewport height")
    parser.add_argument(
        "--npc-id",
        required=True,
        metavar="NPC_ID",
        help="NPC identifier passed to the injected page script",
    )
    parser.add_argument(
        "--clothes",
        default="default",
        help="clothes passed to _internalRendererNpc (default: default)",
    )
    parser.add_argument(
        "--npc-name",
        default=DEFAULT_MOCK_NPC_NAME,
        help="NPC name used by the mocked chat",
    )
    parser.add_argument(
        "--description",
        default=DEFAULT_MOCK_DESCRIPTION,
        help="description used by the mocked chat",
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MOCK_MESSAGE,
        help="message used by the mocked chat",
    )
    parser.add_argument(
        "--action",
        dest="mock_actions",
        action="append",
        help="mocked chat action; repeat for multiple actions",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="destination PNG path (default: outputs/{timestamp}_screenshot.png)",
    )
    parser.add_argument(
        "--js",
        type=Path,
        help="JavaScript file to execute after the page loads",
    )
    parser.add_argument(
        "--wait-for",
        metavar="SELECTOR",
        help="wait until this selector is visible before capture",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=0,
        metavar="MILLISECONDS",
        help="delay after JavaScript and selector wait (default: 0)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the output file if it already exists",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="show the Chromium window while capturing",
    )
    return parser


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ScreenshotError("URL must be a valid http:// or https:// address")
    return url


def default_output_path(now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIRECTORY / f"{timestamp}_screenshot.png"


def _load_sync_playwright() -> Callable:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScreenshotError(
            "Playwright is not installed. Run 'make setup' first."
        ) from exc
    return sync_playwright


def read_javascript(path: Path) -> str:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ScreenshotError(f"JavaScript file not found: {path}")
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ScreenshotError(f"Could not read JavaScript file: {path}") from exc


def capture_screenshot(
    url: str,
    width: int,
    height: int,
    output: Path,
    *,
    npc_id: str,
    clothes: str = "default",
    mock_data: dict[str, object] | None = None,
    javascript: str | None = None,
    wait_for_selector: str | None = None,
    delay_ms: int = 0,
    overwrite: bool = False,
    headless: bool = True,
) -> Path:
    """Capture one viewport and return the resolved output path."""
    validate_url(url)
    if width <= 0:
        raise ScreenshotError("Width must be greater than zero")
    if height <= 0:
        raise ScreenshotError("Height must be greater than zero")
    if delay_ms < 0:
        raise ScreenshotError("Delay must be zero or greater")
    if not npc_id.strip():
        raise ScreenshotError("NPC ID must not be empty")
    if not clothes.strip():
        raise ScreenshotError("Clothes must not be empty")

    destination = output.expanduser().resolve()
    if destination.exists() and not overwrite:
        raise ScreenshotError(
            f"Output file already exists: {output}. Use --overwrite to replace it."
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ScreenshotError(
            f"Could not create output directory: {destination.parent}"
        ) from exc

    sync_playwright = _load_sync_playwright()
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                page = context.new_page()
                page.goto(url, wait_until="load")
                page.evaluate(
                    "(config) => { window.__screenshotConfig = config; }",
                    {
                        "npcId": npc_id,
                        "clothes": clothes,
                        "mock": mock_data,
                    },
                )
                if javascript is not None:
                    page.evaluate(javascript)
                if wait_for_selector:
                    page.wait_for_selector(
                        wait_for_selector,
                        state="visible",
                        timeout=DEFAULT_TIMEOUT_MS,
                    )
                if delay_ms:
                    page.wait_for_timeout(delay_ms)
                page.screenshot(
                    path=str(destination),
                    type="png",
                    full_page=False,
                )
            finally:
                if context is not None:
                    context.close()
                    context = None
                browser.close()
                browser = None
    except ScreenshotError:
        raise
    except Exception as exc:
        raise ScreenshotError(f"Could not capture screenshot: {exc}") from exc
    finally:
        # These guards cover failures raised while entering/exiting Playwright.
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

    return destination


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        javascript = read_javascript(args.js) if args.js else None
        mock_data = {
            "npcName": args.npc_name,
            "description": args.description,
            "message": args.message,
            "actions": args.mock_actions or DEFAULT_MOCK_ACTIONS,
        }
        output = capture_screenshot(
            args.url,
            args.width,
            args.height,
            args.output or default_output_path(),
            npc_id=args.npc_id,
            clothes=args.clothes,
            mock_data=mock_data,
            javascript=javascript,
            wait_for_selector=args.wait_for,
            delay_ms=args.delay,
            overwrite=args.overwrite,
            headless=not args.headed,
        )
    except ScreenshotError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
