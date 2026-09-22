# Viral Maker

Python CLI that adds a caption to an opening video and concatenates it with an
ending video using FFmpeg. The result is normalized to 1080x1920, 60 fps, and
H.264 for vertical social-media videos.

All audio streams from the input videos are discarded. In interactive mode,
the CLI asks whether to add background music downloaded from a TikTok URL, at
100% volume by default. There is no default music: without a URL or an explicit
file, the output video has no soundtrack.

## Requirements

- Python 3.10 or newer
- FFmpeg and FFprobe
- ImageMagick (`magick`, used to compose carousel text and arrows)
- yt-dlp (required to download audio from TikTok)
- `Noto Sans CJK JP` font (included in the Noto CJK package on many systems)
- Chromium managed by Playwright (`make setup` installs it automatically)

## Usage

With the project defaults, this selects a random English caption:

```bash
python3 video_maker.py
```

The video is saved automatically in `outputs` as
`{timestamp}_{video_number}.mp4`, for example
`outputs/20260915_143052_1.mp4`. The number comes from the opening video's
filename.

Select the language, caption, and position:

```bash
python3 video_maker.py --language en --index 3 --position center
```

Answer `Y` or press Enter to add music. The CLI asks for a TikTok URL,
downloads the MP3 to `audio/`, and uses it in the same render. Answer `n` to
render without music. In scripts, use `--music-url URL`, `--music file.mp3` for
an existing track, or `--no-music`. Set the volume with `--music-volume`:

```bash
python3 video_maker.py --music-url 'https://vm.tiktok.com/example/'
python3 video_maker.py --music audio/another-track.mp3 --music-volume 0.15
python3 video_maker.py --no-music
```

Add the localized carousel prompt from `data.json` to the ending video with:

```bash
python3 video_maker.py --final 2 --carousel --carousel-position top
```

The prompt uses three copies of `assets/right-arrow.png`, scaled below the font
height. All paths can also be changed:

```bash
python3 video_maker.py \
  --first videos/1.mp4 \
  --final 2 \
  --final-directory videos/final_videos \
  --data data.json \
  --language ja \
  --position bottom
```

Indexes start at 1. Without `--index`, a caption is selected randomly from the
chosen language. The default language is `en` and the default position is
`top`. Use `--output` to choose a different path and `--overwrite` to replace
an existing output.

Ending videos live in `videos/final_videos` and may use any `.mp4` filename.
They are numbered by creation time, oldest first. Select one with `--final 2`;
without that argument, the first video is used.

## HTTP API

Start the development API with:

```bash
make dev
```

Other available commands:

```bash
make setup     # install dependencies
make api       # start without automatic reload
make test      # run the tests
make generate  # open interactive generation
make help      # list commands
```

Alternatively, install dependencies and start the server manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
```

Interactive documentation is available at `http://localhost:8000/docs`.

Create a video:

```bash
curl -X POST http://localhost:8000/videos/reaction \
  -H 'Content-Type: application/json' \
  -d '{"language":"en","index":3,"position":"center","video":2,"final":2,"carousel":true,"carousel_position":"top","music":true,"music_filename":"7559553583222607107.mp3","music_volume":1.0,"npc_id":"ed9721fb-fa6c-4e66-85a0-c3dd0ba2a5df","clothes":"default","npc_name":"Hana","description":"Scene description","message":"Dialogue message","actions":["First action","Second action"]}'
```

Processing is synchronous: the response is returned when the video is ready.
It contains the ID, selected caption, and download URL. Check
`GET /videos/{id}` or download it with `GET /videos/{id}/download`. Available
opening videos, ending videos, and tracks are listed by `GET /videos`,
`GET /finals`, and `GET /audios` respectively.

When `carousel` is `true`, the same request also generates the PNG for the next
slide. In that case, `npc_id`, `clothes`, `npc_name`, `description`, `message`,
and `actions` are required. The client URL and dimensions use these defaults:

```json
{
  "client_url": "http://127.0.0.1:3000/play",
  "screenshot_width": 540,
  "screenshot_height": 960
}
```

All three fields can be changed in the payload. The response includes
`screenshot_id` and `screenshot_url`; `GET /videos/{id}` returns this relation
as well. Download the PNG from `GET /screenshots/{screenshot_id}/download`.
Videos, screenshots, and generation parameters are associated in the local
`generation_assets.db` SQLite database.

Get the mock-dialogue history from newest to oldest:

```bash
curl http://localhost:8000/screenshots/history
```

Filter by language (`en`, `pt`, or `ja`) with the `language` parameter:

```bash
curl 'http://localhost:8000/screenshots/history?language=en'
```

Each item contains the video and screenshot IDs and URLs, language, `npc_id`,
`clothes`, client URL, dimensions, name, description, message, actions, and
creation date. Older records created before the language field return
`language` as `null`.

List opening videos:

```bash
curl http://localhost:8000/videos
```

Each item contains `number`, `filename`, `size_bytes`, and its streaming `url`.
The list includes every `.mp4` directly under `videos`, excludes
`videos/final_videos`, and numbers files by creation time, oldest first. Pass
the chosen `number` in the `video` field of `POST /videos/reaction`; the first
video is used when omitted.

List available tracks:

```bash
curl http://localhost:8000/audios
```

Each item contains `filename`, `size_bytes`, and `url`; internal server paths
are not returned. Pass that exact `filename` as `music_filename` to
`POST /videos/reaction`. There is no default track: if `music_filename` is
omitted, the video has no music even when `music` is `true`.

Each item also includes TikTok engagement metrics in `views`, `likes`,
`comments`, and `shares` when known. These fields are `null` when no metrics
are associated with the file.

Download TikTok audio through the API and record its engagement metrics:

```bash
curl -X POST http://localhost:8000/audios \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.tiktok.com/@user/video/123"}'
```

The HTTP 201 response has the same format as `GET /audios`, with metrics filled
when TikTok exposes them. The endpoint accepts `overwrite` (default `false`)
and `cookies_from_browser` for videos that require an authenticated session.
An invalid or failed download returns HTTP 400 with the reason.

List captions for a language:

```bash
curl 'http://localhost:8000/data?language=en'
```

The response contains `language`, the `carousel` text, and the `data` list.
Accepted languages are `en`, `pt`, and `ja`.

List generated videos from newest to oldest:

```bash
curl http://localhost:8000/outputs
```

Each item contains `id`, `filename`, `size_bytes`, `created_at`, a streaming
`url`, `download_url`, and `generation`. The `generation` object records the
opening and ending videos, language, selected caption, positions, carousel,
and music in SQLite. Videos with a next slide also contain `screenshot_id` and
`screenshot_url`. Older files without a SQLite record return
`generation: null`.

`GET /videos/{id}` also returns the `generation` object with the status and
optional screenshot association.

Media directories are exposed directly with streaming support:

```text
GET /media/audios/{filename}
GET /media/videos/{path}
GET /media/outputs/{filename}
```

## Download TikTok audio

Only download your own videos or content you are authorized to use. The
command displays progress, extracts the audio as MP3, and saves it as
`audio/{tiktok_id}.mp3`:

```bash
python3 download_tiktok_audio.py 'https://www.tiktok.com/@user/video/123'
```

For videos that require an authenticated session, import browser cookies:

```bash
python3 download_tiktok_audio.py URL --cookies-from-browser firefox
```

Then use the path printed by the command in a render:

```bash
python3 video_maker.py --music audio/123.mp3
```

## Webpage screenshots

Capture a page viewport as PNG using headless Chromium:

```bash
.venv/bin/python screenshot.py https://example.com \
  --width 1080 \
  --height 1920 \
  --npc-id NPC_ID \
  --output screenshot.png
```

The `--output` argument is optional. When omitted, the file is created in
`outputs/` using the video timestamp convention, for example
`outputs/20260915_143052_screenshot.png`.

Capture uses 2x supersampling and keeps the PNG at the requested dimensions.
This improves sharpness without changing the size or component layout.

You can also execute JavaScript after page load, wait for a visible selector,
and apply an additional delay in milliseconds:

```bash
.venv/bin/python screenshot.py https://example.com \
  --width 1080 \
  --height 1920 \
  --npc-id NPC_ID \
  --js inject.js \
  --wait-for '#marketing-card' \
  --delay 2000 \
  --output screenshot.png
```

When `--wait-for` and `--delay` are used together, the selector is awaited
first. Existing output is replaced only with `--overwrite`. Chromium and the
page context are closed automatically even on errors.

Add `--headed` to watch the automation in a visible Chromium window. Mock chat
data can also be overridden through the CLI; repeat `--action` for multiple
actions:

```bash
.venv/bin/python screenshot.py http://127.0.0.1:3000/play \
  --width 540 \
  --height 960 \
  --npc-id NPC_ID \
  --clothes school_uniform \
  --npc-name 'Hana' \
  --description 'Scene description' \
  --message 'Dialogue message' \
  --action 'First action' \
  --action 'Second action' \
  --js assets/inject.js \
  --headed
```

Without those optional fields, the default Japanese text defined in
`screenshot.py` is used. The default outfit is `default`.

If dependencies were installed manually, also install Chromium for Playwright:

```bash
.venv/bin/playwright install chromium
```

## Tests

```bash
python3 -m unittest -v
```
