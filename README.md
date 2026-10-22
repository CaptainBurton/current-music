# Current

Current is a private, self-hosted music and podcast player with a Spotify-inspired interface and a cyan `#00c3ff` default theme. It scans your own music folders, streams tracks through the browser, and keeps listening data on your server.

## Run with Docker Compose

1. Open [compose.yaml](compose.yaml) and replace `/path/to/your/music` with the absolute path to your music collection. The folder is mounted read-only at `/music` in the container. To add more collections, add more volume mounts beneath `/music`, such as `/srv/jazz:/music/jazz:ro`.
2. Run `docker compose up -d --build`. The first build installs FFmpeg and takes a few minutes; later builds reuse cached layers and downloads, so they take seconds. Restarts finish within about 3 seconds even while someone is listening.
3. Open `http://YOUR_SERVER_IP:8787`. The first visit shows setup for the first admin account. Enter `/music` as the optional folder path, or add locations later in **Admin Settings**.
4. Scan the library from **Admin Settings → Music library**.

The SQLite database, cached album art, user avatars, and artist images live in `./data`. Back up that folder to preserve accounts, playlists, favorites, artist profiles, and album matches. Music files are never modified by scans.

For access outside a trusted local network, put Current behind an HTTPS reverse proxy and set `CURRENT_SECURE_COOKIES: "1"` in the Compose environment. Use access controls on the proxy as appropriate for your server.

## Library organization

Current scans MP3, FLAC, M4A, M4B, MP4 audio, AAC, OGG, Opus, WAV, AIFF, WMA, APE, WavPack, Musepack, and DSF files. Files the browser cannot decode are converted to MP3 for playback with FFmpeg. Converted copies are cached under `./data/transcodes`; source files are never modified. The Docker image includes FFmpeg. For local development, install FFmpeg separately to play formats your browser does not support. Embedded tags are preferred by default. Missing fields are inferred from a configurable path pattern, initially:

```text
{artist}/{album}/{track} - {title}
```

For example, `/music/Nina Simone/Little Girl Blue/01 - Mood Indigo.flac` becomes artist **Nina Simone**, album **Little Girl Blue**, and track **Mood Indigo**. Choose **Folder names first** in a location's settings to prefer that structure over tags. Current also reads embedded artwork and common folder images named `cover.jpg`, `folder.jpg`, or `front.jpg`.

Library scans can be run on demand. They update changed files and remove entries for files that disappeared from a scanned location. They do not delete audio files.

## Free album metadata

Current gets release years and genres from the [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API) and cover art from the [Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API). No API key or paid account is required. Set a contact email during first-run setup, in **Admin Settings → Online metadata**, or through `MUSICBRAINZ_CONTACT` in Compose. MusicBrainz requires an identifying User-Agent and no more than one request per second; Current observes that limit.

Enable online matching during a scan for high-confidence matches, or open an album and select **Find album metadata** to choose a match yourself. Matching sends album and artist search terms to MusicBrainz. Artwork is cached in `./data/covers`.

## Accounts and listening

The first account is an admin. Admins add music locations, scan the library, match album metadata, manage artist profiles, and create or remove users. Regular users can browse, search, listen, like songs, follow podcasts, and create playlists. Each user has their own likes, playlists, podcast subscriptions, display name, profile picture, and theme; the music collection is shared.

Home includes top artists and albums from your library, with your liked music ranked first. The bottom player supports queue playback, shuffle, repeat, seeking, volume, and media keys where supported. Right-click a song on desktop or use its **More options** button on a phone to choose **Play Next** or **Add to Queue**. The **Queue** view shows the upcoming order and lets you remove items. The iOS 6 Classic Now Playing view moves covers smoothly between positions and reflects the current and adjacent covers. Audio files are served only to signed-in users, with byte-range requests for seeking. The Back button follows pages opened inside Current and goes Home when a page was opened directly.

## Profiles, themes, and artist pages

Listeners can change their display name and upload a PNG, JPEG, or WebP profile picture in **Your Settings**. The login username stays the same. Theme choices are saved per account:

- **Current:** the default dark interface with cyan accents.
- **iOS 6 Classic:** brushed metal controls, blue gloss, Helvetica Neue, and a reflected Cover Flow view.
- **Sabrina Carpenter:** a dark editorial style with warm gold, serif type, and the supplied signature prominently displayed in the sidebar and on Home.
- **Spotify:** a dark green theme with the supplied Spotify icon and a familiar sans-serif look.
- **Doja Cat:** lavender chrome and scanlines, hot pink serif type, and the supplied signature in the sidebar, on Home, and in Now Playing.

Artists appear automatically from album artist tags during scans. In **Admin Settings → Artists**, admins can edit an artist's name and bio, upload a square portrait and a wide cover image, and assign existing releases as albums or singles. Assignments remain attached to the scanned release after later scans. New releases with one track are shown as singles by default; admins can change that classification.

## Lyrics and podcasts

Open **Now Playing** for sing-along lyrics. Put an `.lrc` file beside a song with the same filename, such as `01 - Song.lrc` for `01 - Song.mp3`, to use your own timed lyrics. If no sidecar is present, Current can look up free plain or synchronized lyrics from [LRCLIB](https://lrclib.net/docs). Timed lines highlight as the song plays. An admin can also paste plain or timed LRC lyrics in Now Playing. Some songs may have no online match.

In **Podcasts**, paste a public HTTPS RSS feed URL to follow a show. Current reads its episodes and plays their HTTPS audio enclosures in the browser. Subscriptions and playback positions are saved per listener. Use **Refresh feed** on a show to fetch new episodes. Podcasts stream from their publishers; Current does not copy the audio into your music folder.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
CURRENT_MUSIC_ROOTS=/absolute/path/to/music .venv/bin/uvicorn app.main:app --reload --port 8787
```

`CURRENT_DATA_DIR` overrides the data directory, which defaults to `./data`. `CURRENT_MUSIC_ROOTS` is a colon-separated list of allowed server roots on Unix. Admins can add any existing folder within those roots. `CURRENT_SECURE_COOKIES=1` marks login cookies Secure for HTTPS deployments.

## Branding

Current uses its own name and icon. The interface draws on familiar music-player patterns while keeping a distinct mark and color.
