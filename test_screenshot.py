import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import screenshot


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.directory = Path(self.temp_directory.name)

    def playwright_mocks(self):
        manager = MagicMock()
        playwright = manager.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        factory = MagicMock(return_value=manager)
        return factory, playwright, browser, context, page

    def test_parser_accepts_all_options(self):
        args = screenshot.build_parser().parse_args(
            [
                "https://example.com",
                "--width",
                "1080",
                "--height",
                "1920",
                "--npc-id",
                "npc-123",
                "--clothes",
                "school_uniform",
                "--npc-name",
                "Aiko",
                "--description",
                "At school",
                "--message",
                "Hello",
                "--action",
                "Talk",
                "--action",
                "Leave",
                "--output",
                "capture.png",
                "--js",
                "inject.js",
                "--wait-for",
                "#ready",
                "--delay",
                "2000",
                "--overwrite",
                "--headed",
            ]
        )
        self.assertEqual(args.width, 1080)
        self.assertEqual(args.height, 1920)
        self.assertEqual(args.npc_id, "npc-123")
        self.assertEqual(args.clothes, "school_uniform")
        self.assertEqual(args.npc_name, "Aiko")
        self.assertEqual(args.description, "At school")
        self.assertEqual(args.message, "Hello")
        self.assertEqual(args.mock_actions, ["Talk", "Leave"])
        self.assertEqual(args.wait_for, "#ready")
        self.assertEqual(args.delay, 2000)
        self.assertTrue(args.overwrite)
        self.assertTrue(args.headed)

    def test_default_output_uses_video_timestamp_format(self):
        moment = datetime(2026, 9, 15, 14, 30, 52)
        self.assertEqual(
            screenshot.default_output_path(moment),
            Path("outputs/20260915_143052_screenshot.png"),
        )

    def test_output_is_optional(self):
        args = screenshot.build_parser().parse_args(
            [
                "https://example.com",
                "--width",
                "1080",
                "--height",
                "1920",
                "--npc-id",
                "npc-123",
            ]
        )
        self.assertIsNone(args.output)

    def test_capture_runs_steps_in_order_and_returns_resolved_path(self):
        factory, playwright, browser, context, page = self.playwright_mocks()
        calls = []
        page.goto.side_effect = lambda *args, **kwargs: calls.append("goto")
        page.evaluate.side_effect = lambda *args, **kwargs: calls.append(
            "npc_id" if len(args) == 2 else "javascript"
        )
        page.wait_for_selector.side_effect = lambda *args, **kwargs: calls.append(
            "selector"
        )
        page.wait_for_timeout.side_effect = lambda *args, **kwargs: calls.append(
            "delay"
        )
        page.screenshot.side_effect = lambda *args, **kwargs: calls.append("screenshot")
        output = self.directory / "nested" / "capture.any-extension"

        with patch.object(screenshot, "_load_sync_playwright", return_value=factory):
            result = screenshot.capture_screenshot(
                "https://example.com",
                1080,
                1920,
                output,
                npc_id="npc-123",
                javascript="document.body.dataset.ready = 'yes'",
                wait_for_selector="#ready",
                delay_ms=2000,
            )

        self.assertEqual(
            calls,
            ["goto", "npc_id", "javascript", "selector", "delay", "screenshot"],
        )
        self.assertEqual(result, output.resolve())
        playwright.chromium.launch.assert_called_once_with(headless=True)
        browser.new_context.assert_called_once_with(
            viewport={"width": 1080, "height": 1920},
            device_scale_factor=2,
        )
        page.goto.assert_called_once_with("https://example.com", wait_until="load")
        page.evaluate.assert_any_call(
            "(config) => { window.__screenshotConfig = config; }",
            {"npcId": "npc-123", "clothes": "default", "mock": None},
        )
        page.wait_for_selector.assert_called_once_with(
            "#ready", state="visible", timeout=30_000
        )
        page.screenshot.assert_called_once_with(
            path=str(output.resolve()), type="png", full_page=False, scale="css"
        )
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()

    def test_capture_skips_optional_steps(self):
        factory, _, _, _, page = self.playwright_mocks()
        with patch.object(screenshot, "_load_sync_playwright", return_value=factory):
            screenshot.capture_screenshot(
                "http://localhost:8000",
                320,
                240,
                self.directory / "capture.png",
                npc_id="npc-123",
            )
        page.evaluate.assert_called_once_with(
            "(config) => { window.__screenshotConfig = config; }",
            {"npcId": "npc-123", "clothes": "default", "mock": None},
        )
        page.wait_for_selector.assert_not_called()
        page.wait_for_timeout.assert_not_called()

    def test_browser_is_closed_when_capture_fails(self):
        factory, _, browser, context, page = self.playwright_mocks()
        page.screenshot.side_effect = RuntimeError("disk error")
        with patch.object(screenshot, "_load_sync_playwright", return_value=factory):
            with self.assertRaisesRegex(screenshot.ScreenshotError, "disk error"):
                screenshot.capture_screenshot(
                    "https://example.com",
                    100,
                    200,
                    self.directory / "capture.png",
                    npc_id="npc-123",
                )
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()

    def test_validates_inputs_before_starting_browser(self):
        invalid_cases = [
            ("ftp://example.com", 100, 100, 0, "URL"),
            ("https://example.com", 0, 100, 0, "Width"),
            ("https://example.com", 100, -1, 0, "Height"),
            ("https://example.com", 100, 100, -1, "Delay"),
        ]
        for url, width, height, delay, message in invalid_cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(screenshot.ScreenshotError, message):
                    screenshot.capture_screenshot(
                        url,
                        width,
                        height,
                        self.directory / "capture.png",
                        npc_id="npc-123",
                        delay_ms=delay,
                    )

    def test_existing_output_requires_overwrite(self):
        output = self.directory / "capture.png"
        output.write_bytes(b"old")
        with self.assertRaisesRegex(screenshot.ScreenshotError, "--overwrite"):
            screenshot.capture_screenshot(
                "https://example.com", 100, 100, output, npc_id="npc-123"
            )

        factory, _, _, _, page = self.playwright_mocks()
        with patch.object(screenshot, "_load_sync_playwright", return_value=factory):
            screenshot.capture_screenshot(
                "https://example.com",
                100,
                100,
                output,
                npc_id="npc-123",
                overwrite=True,
            )
        page.screenshot.assert_called_once()

    def test_blank_npc_id_is_rejected(self):
        with self.assertRaisesRegex(screenshot.ScreenshotError, "NPC ID"):
            screenshot.capture_screenshot(
                "https://example.com",
                100,
                100,
                self.directory / "capture.png",
                npc_id="   ",
            )

    def test_read_javascript(self):
        source = self.directory / "inject.js"
        source.write_text("document.title = 'Marketing';", encoding="utf-8")
        self.assertEqual(
            screenshot.read_javascript(source), "document.title = 'Marketing';"
        )

    def test_missing_javascript_file_is_rejected(self):
        with self.assertRaisesRegex(screenshot.ScreenshotError, "not found"):
            screenshot.read_javascript(self.directory / "missing.js")

    def test_main_reads_javascript_and_calls_reusable_function(self):
        source = self.directory / "inject.js"
        source.write_text("window.ready = true", encoding="utf-8")
        output = self.directory / "capture.png"
        with patch.object(
            screenshot, "capture_screenshot", return_value=output.resolve()
        ) as capture:
            result = screenshot.main(
                [
                    "https://example.com",
                    "--width",
                    "1080",
                    "--height",
                    "1920",
                    "--npc-id",
                    "npc-123",
                    "--js",
                    str(source),
                    "--output",
                    str(output),
                ]
            )
        self.assertEqual(result, 0)
        capture.assert_called_once_with(
            "https://example.com",
            1080,
            1920,
            output,
            npc_id="npc-123",
            clothes="default",
            mock_data={
                "npcName": screenshot.DEFAULT_MOCK_NPC_NAME,
                "description": screenshot.DEFAULT_MOCK_DESCRIPTION,
                "message": screenshot.DEFAULT_MOCK_MESSAGE,
                "actions": screenshot.DEFAULT_MOCK_ACTIONS,
            },
            javascript="window.ready = true",
            wait_for_selector=None,
            delay_ms=0,
            overwrite=False,
            headless=True,
        )

    def test_headed_mode_shows_browser(self):
        factory, playwright, _, _, _ = self.playwright_mocks()
        with patch.object(screenshot, "_load_sync_playwright", return_value=factory):
            screenshot.capture_screenshot(
                "https://example.com",
                1080,
                1920,
                self.directory / "capture.png",
                npc_id="npc-123",
                headless=False,
            )
        playwright.chromium.launch.assert_called_once_with(headless=False)


if __name__ == "__main__":
    unittest.main()
