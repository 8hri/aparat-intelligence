# Aparat Extraction via the Browser API (yt-dlp fallback)

Use when `main.py metadata` / `main.py download` fail with a **yt-dlp extractor
error** (`Unable to extract og:title` and similar). Cause: Aparat's video
pages are Next.js SPAs with no server-rendered meta tags, so both yt-dlp's
extractor and the skill's static scrape fallback find nothing to parse.
`aparat.com` itself is reachable over HTTPS — this is an extractor
limitation, not a network outage. The page loads in the browser tool.

## The working path

1. **Open the page in the browser** so its JS runs:

   ```python
   new_tab("https://www.aparat.com/v/VIDEO_ID")
   wait_for_load()
   # give the SPA a few seconds to hydrate
   ```

2. **Query Aparat's own video API from the page's fetch context.** The same
   endpoint rejects host-side curl (`Wrong System Module Action Called`) but
   answers an in-page `fetch` with same-origin credentials:

   ```python
   data = js('''(async () => {
     const resp = await fetch(
       "https://www.aparat.com/api/v0/videos/VIDEO_ID?pr=1&mf=1&referer=direct",
       {headers: {"Accept": "application/json"}, credentials: "same-origin"});
     return await resp.text();
   })()''')
   ```

   `data.data` holds the full record: `title`, `description` (often with a
   chapter timeline in `0:00:00\ttitle` lines), `uid`, `duration`,
   `visit_cnt`, `tags`, `file_link_all`, `hls_link`, `sdate_real`.

3. **Take a direct CDN URL** from `data.data.file_link_all[].urls[]`
   (`profile` of each entry is the quality). These carry a `wmsAuthSign`
   token that **expires within minutes to hours** — download immediately,
   and fetch a fresh URL rather than reusing one from a previous session.

4. **curl the URL into the cache**, then run the ordinary local pipeline on
   the local file:

   ```bash
   CACHE=~/.cache/aparat-intelligence/videos/VIDEO_ID
   mkdir -p "$CACHE"
   curl -sS -L --max-time 240 -A "Mozilla/5.0" "<direct_url>" \
       -o "$CACHE/source.mp4"
   python <skill_dir>/scripts/main.py chunks "$CACHE/audio.mp3" "$CACHE/chunks"
   # audio/frames extraction also work directly on source.mp4
   ```

   If only `hls_link` is usable, `ffmpeg -i <m3u8> -c copy out.mp4` pulls the
   stream (same token expiry applies).

## Finding the endpoint on a changed site

If the `/api/v0/videos/...` shape changes, recover it:

- Grep the page's `_next/static/chunks/...js` bundles for `api`/`/v1`/`/v0`
  path patterns, or
- Read `performance.getEntriesByType('resource')` from the loaded page — it
  lists every endpoint and CDN URL the SPA actually fetched.

## Pitfalls

- **Stale page context:** after the tab sits idle or the session is
  compressed, in-page `fetch` starts failing with "Failed to fetch".
  Re-run `new_tab(url)` on the same URL to restore it — do not conclude the
  site is down before retrying navigation once.
- **Token expiry:** `wmsAuthSign` URLs are short-lived. Never persist one
  across sessions and never tell the user to "download it later" from a
  saved URL.
- **VTT subtitles are not transcripts:** `<videohash>_t.vtt` on Aparat is a
  thumbnail sprite-sheet storyboard (image coordinates), not timed text.
  Transcription still needs STT.

## Provenance

Metadata obtained this way is the **metadata source** (like the official
description), not a transcript and not visual analysis. The chapter timeline
parsed from `description` is publisher-authored — say so when citing it.
