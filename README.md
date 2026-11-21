# smartsort_tech

Automated YouTube Shorts and weekly compilation pipeline.
## Setup

1. Install Python dependencies: `python -m pip install -r requirements.txt`.
2. Install FFmpeg and ensure both `ffmpeg` and `ffprobe` are on `PATH`.
3. For website recordings, install Chromium once with `python -m playwright install chromium`.
4. Set `GEMINI_API_KEY` in the environment to use the idea generator.
5. Optionally set `PIXABAY_API_KEY`; without it, add vertical or landscape MP4 clips to `assets/fallback_broll/`.

In PowerShell, set a key for the current terminal session with:

```powershell
$env:GEMINI_API_KEY = "your-key"
$env:PIXABAY_API_KEY = "your-key"
```

`GEMINI_MODEL` can override the default `gemini-3.1-flash-lite` model.

## Generate, render, and schedule

Add one topic per line to `ideas.txt`, then run:

```powershell
python idea_generator.py
python build_short.py
```

The generator appends validated entries to `videos.json`. The builder prefers an
existing `local_video`, then records `urls` with Playwright, then fetches B-roll
for `broll_queries`, and finally animates supplied `images` with a Ken Burns
effect. It writes MP4s to `output/` and marks queue entries `rendered`; captions
are three words at a time with the active word highlighted in yellow.
The browser recorder runs at native 1080x1920; some sites may still show
anti-bot or sign-in screens in automated recordings.
Example Shorts created for validation are marked `preview` in `videos.json` and
will not be uploaded automatically.

YouTube publishing is deliberately a separate, explicit action:

1. Enable the YouTube Data API and YouTube Analytics API in a Google Cloud
   project and create an OAuth Desktop app client. Save the client JSON as
   `client_secrets.json` in this folder, or set `YOUTUBE_CLIENT_SECRETS` to its
   path.
2. Run `python auth_setup.py`. Sign in to Google and approve the YouTube upload,
   read-only YouTube, and read-only Analytics scopes. The script requests offline
   access and saves a refresh token in `token.json`.
3. Add the complete token JSON as the `YOUTUBE_TOKEN_JSON` Actions secret. Keep
   both OAuth JSON files out of Git; `.gitignore` already excludes them.

After authorization, `python build_short.py --publish` uploads eligible videos
as private and schedules them using each entry's future `publish_at_utc`.

Uploaded entries are marked `done` and store their `youtube_video_id`.
`python build_short.py --single` retains the original one-off workflow and
expects `script.txt` plus `raw_screen.mp4`.

## Analytics and weekly compilations

`python analytics_loop.py` reads views and average view percentage from the
YouTube Analytics API for the last 14 days, ranks videos by views multiplied by
retention, asks Gemini (default model `gemini-3.1-flash-lite`) for five related
topic ideas, and appends generated Shorts to the queue. If there are no analytics
rows yet, it uses starter topics.
The first local run requires OAuth consent for both
`youtube.readonly` and `yt-analytics.readonly`; reauthorize with the updated
scopes and replace the `YOUTUBE_TOKEN_JSON` GitHub Actions secret. In Google
Cloud, enable the YouTube Analytics API as well as the YouTube Data API.

`python compile_weekly.py` creates a 16:9 compilation from at least three
uploaded Shorts not previously compiled. Each short is centered over a blurred
background, transitions are crossfaded, and the description includes chapters.
Add `--publish` to schedule the compilation for the next Saturday; local runs
only create an MP4 and do not mark queue entries as compiled.

The weekly GitHub Actions job runs analytics, generates and schedules Shorts,
then builds and schedules a compilation. Configure `GEMINI_API_KEY`,
`PIXABAY_API_KEY`, `YOUTUBE_CLIENT_SECRETS`, and an updated
`YOUTUBE_TOKEN_JSON` in repository Actions secrets before enabling it.

## Notes

- Review Gemini-generated scripts, links, claims, and stock footage licenses
  before publishing.
- `.env`, OAuth client files, and OAuth tokens are local credentials/configuration
  and should not be shared or committed. `videos.json` is the tracked queue used
  by the pipeline and GitHub Actions.
- Pixabay or fallback footage is only used when URL and local-video sources are
  absent. If no Pixabay result and no local fallback exists, the builder stops
  with an actionable error instead of creating a video without visuals.
