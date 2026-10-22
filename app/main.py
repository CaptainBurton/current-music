from __future__ import annotations

import hashlib
import html
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mutagen import File as MutagenFile


APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CURRENT_DATA_DIR", APP_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR = DATA_DIR / "covers"
COVERS_DIR.mkdir(exist_ok=True)
AVATARS_DIR = DATA_DIR / "avatars"
AVATARS_DIR.mkdir(exist_ok=True)
ARTIST_IMAGES_DIR = DATA_DIR / "artists"
ARTIST_IMAGES_DIR.mkdir(exist_ok=True)
TRANSCODES_DIR = DATA_DIR / "transcodes"
TRANSCODES_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "current.db"
MUSIC_ROOTS = [Path(p).expanduser().resolve() for p in os.getenv("CURRENT_MUSIC_ROOTS", "/music").split(os.pathsep) if p]
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".opus",
                    ".wav", ".aiff", ".aif", ".wma", ".ape", ".wv", ".mpc", ".dsf"}
COOKIE_NAME = "current_session"
SESSION_SECONDS = 60 * 60 * 24 * 30
scan_lock = threading.Lock()
scan_state = {"running": False, "phase": "idle", "scanned": 0, "added": 0, "updated": 0, "total": 0, "message": "", "finished_at": None}
mb_lock = threading.Lock()
mb_last_call = 0.0
transcode_locks_guard = threading.Lock()
transcode_locks = {}
log = logging.getLogger("uvicorn.error")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # WAL keeps the database consistent at NORMAL; FULL fsyncs on every commit and slows scans badly.
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS locations (
          id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, mode TEXT NOT NULL DEFAULT 'tags_first',
          pattern TEXT NOT NULL DEFAULT '{artist}/{album}/{track} - {title}',
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS albums (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
          year TEXT, genre TEXT, cover_path TEXT, mbid TEXT, source TEXT NOT NULL DEFAULT 'local',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(title, artist)
        );
        CREATE TABLE IF NOT EXISTS artist_profiles (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          bio TEXT NOT NULL DEFAULT '', portrait_type TEXT, banner_type TEXT,
          image_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tracks (
          id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          path TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL,
          album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
          disc INTEGER NOT NULL DEFAULT 1, track_no INTEGER NOT NULL DEFAULT 0,
          duration REAL NOT NULL DEFAULT 0, mtime REAL NOT NULL, size INTEGER NOT NULL,
          codec TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id, disc, track_no);
        CREATE INDEX IF NOT EXISTS tracks_title ON tracks(title);
        CREATE TABLE IF NOT EXISTS favorites (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          PRIMARY KEY(user_id, track_id)
        );
        CREATE TABLE IF NOT EXISTS playlists (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS playlist_tracks (
          playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL,
          PRIMARY KEY(playlist_id, track_id)
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL, theme TEXT NOT NULL DEFAULT 'current',
          avatar_type TEXT, avatar_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS track_lyrics (
          track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
          synced TEXT, plain TEXT, source TEXT NOT NULL, fetched_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS podcasts (
          id INTEGER PRIMARY KEY, feed_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
          author TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
          artwork_url TEXT, website_url TEXT, refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS podcast_episodes (
          id INTEGER PRIMARY KEY, podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
          guid TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          published_at TEXT, duration REAL NOT NULL DEFAULT 0, audio_url TEXT NOT NULL,
          artwork_url TEXT, UNIQUE(podcast_id,guid)
        );
        CREATE INDEX IF NOT EXISTS podcast_episodes_recent ON podcast_episodes(published_at DESC);
        CREATE TABLE IF NOT EXISTS podcast_subscriptions (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id,podcast_id)
        );
        CREATE TABLE IF NOT EXISTS episode_progress (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          episode_id INTEGER NOT NULL REFERENCES podcast_episodes(id) ON DELETE CASCADE,
          position REAL NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id,episode_id)
        );
        """)
        album_columns = {row["name"] for row in conn.execute("PRAGMA table_info(albums)")}
        if "source_artist" not in album_columns:
            conn.execute("ALTER TABLE albums ADD COLUMN source_artist TEXT")
        if "artist_id" not in album_columns:
            conn.execute("ALTER TABLE albums ADD COLUMN artist_id INTEGER REFERENCES artist_profiles(id)")
        if "release_type" not in album_columns:
            conn.execute("ALTER TABLE albums ADD COLUMN release_type TEXT")
        conn.execute("UPDATE albums SET source_artist=artist WHERE source_artist IS NULL")
        conn.execute("INSERT OR IGNORE INTO artist_profiles(name) SELECT DISTINCT artist FROM albums WHERE artist_id IS NULL")
        conn.execute("""UPDATE albums SET artist_id=(SELECT id FROM artist_profiles WHERE name=albums.artist)
          WHERE artist_id IS NULL""")
        conn.execute("CREATE INDEX IF NOT EXISTS albums_artist ON albums(artist_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS tracks_location ON tracks(location_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS tracks_recent ON tracks(created_at)")


init_db()
app = FastAPI(title="Current", docs_url=None, redoc_url=None)


def fail(message, status=400):
    raise HTTPException(status_code=status, detail=message)


def clean_text(value, max_len=200):
    return str(value or "").strip()[:max_len]


def password_hash(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${key.hex()}"


def password_ok(password, stored):
    try:
        _, rounds, salt, key = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(candidate, bytes.fromhex(key))
    except (ValueError, TypeError):
        return False


def create_session(conn, user_id):
    token = secrets.token_urlsafe(40)
    conn.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
    conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + SESSION_SECONDS))
    return token


def set_cookie(response, token):
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=os.getenv("CURRENT_SECURE_COOKIES") == "1", samesite="lax", path="/")
    return response


def user_payload(conn, user_id):
    row = conn.execute("""SELECT users.id,users.username,users.role,
      COALESCE(user_profiles.display_name,users.username) AS display_name,
      COALESCE(user_profiles.theme,'current') AS theme,
      user_profiles.avatar_type,user_profiles.avatar_version
      FROM users LEFT JOIN user_profiles ON user_profiles.user_id=users.id
      WHERE users.id=?""", (user_id,)).fetchone()
    if not row:
        fail("User not found", 404)
    result = dict(row)
    result["avatar_url"] = (f"/api/users/{user_id}/avatar?v={result['avatar_version']}"
                            if result.pop("avatar_type") else None)
    result.pop("avatar_version", None)
    return result


def current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        fail("Sign in to continue", 401)
    with db() as conn:
        row = conn.execute("""SELECT users.id FROM sessions
                           JOIN users ON users.id=sessions.user_id
                           WHERE token_hash=? AND expires_at>?""",
                           (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if not row:
            fail("Session expired", 401)
        return user_payload(conn, row["id"])


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        fail("Admin access required", 403)
    return user


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
    return await call_next(request)


@app.get("/api/status")
def status():
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"needs_setup": count == 0, "name": "Current"}


@app.post("/api/setup")
async def setup(request: Request):
    body = await request.json()
    username = clean_text(body.get("username"), 40)
    password = str(body.get("password") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        fail("Username must be 3–40 letters, numbers, dots, dashes, or underscores")
    if len(password) < 10:
        fail("Password must be at least 10 characters")
    contact = clean_text(body.get("musicbrainz_contact"), 200)
    if contact and not (re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact) or contact.startswith("https://")):
        fail("Use a contact email or HTTPS website for MusicBrainz identification")
    # PBKDF2 takes a moment; hashing in a worker thread keeps audio streams flowing meanwhile.
    hashed = await run_in_threadpool(password_hash, password)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            fail("Setup is already complete", 409)
        user_id = conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'admin')",
                               (username, hashed)).lastrowid
        conn.execute("INSERT INTO user_profiles(user_id,display_name) VALUES(?,?)", (user_id, username))
        if contact:
            conn.execute("INSERT INTO settings(key,value) VALUES('musicbrainz_contact',?)", (contact,))
        token = create_session(conn, user_id)
        payload = user_payload(conn, user_id)
    return set_cookie(JSONResponse(payload), token)


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    username = clean_text(body.get("username"), 40)
    password = str(body.get("password") or "")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not await run_in_threadpool(password_ok, password, row["password_hash"]):
        fail("Invalid username or password", 401)
    with db() as conn:
        token = create_session(conn, row["id"])
        response = JSONResponse(user_payload(conn, row["id"]))
    return set_cookie(response, token)


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


@app.put("/api/me/profile")
async def update_profile(request: Request, user=Depends(current_user)):
    name = clean_text((await request.json()).get("display_name"), 50)
    if not name:
        fail("Display name is required")
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles(user_id,display_name) VALUES(?,?)
          ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name""", (user["id"], name))
        return user_payload(conn, user["id"])


@app.put("/api/me/theme")
async def update_theme(request: Request, user=Depends(current_user)):
    theme = (await request.json()).get("theme")
    if theme not in ("current", "ios6", "sabrina", "spotify", "doja"):
        fail("Unknown theme")
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles(user_id,display_name,theme) VALUES(?,?,?)
          ON CONFLICT(user_id) DO UPDATE SET theme=excluded.theme""",
                     (user["id"], user["display_name"], theme))
        return user_payload(conn, user["id"])


@app.put("/api/me/avatar")
async def upload_avatar(request: Request, user=Depends(current_user)):
    if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > 2_000_000:
        fail("Choose an image under 2 MB")
    data = await request.body()
    if not data or len(data) > 2_000_000:
        fail("Choose an image under 2 MB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        image_type, extension = "image/png", "png"
    elif data.startswith(b"\xff\xd8\xff"):
        image_type, extension = "image/jpeg", "jpg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image_type, extension = "image/webp", "webp"
    else:
        fail("Choose a PNG, JPEG or WebP image")
    path = AVATARS_DIR / f"{user['id']}.{extension}"
    temp = AVATARS_DIR / f".{user['id']}.{secrets.token_hex(8)}.tmp"
    temp.write_bytes(data)
    temp.replace(path)
    for old in AVATARS_DIR.glob(f"{user['id']}.*"):
        if old != path:
            old.unlink(missing_ok=True)
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles(user_id,display_name,avatar_type,avatar_version)
          VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
          avatar_type=excluded.avatar_type,avatar_version=excluded.avatar_version""",
                     (user["id"], user["display_name"], image_type, int(time.time() * 1000)))
        return user_payload(conn, user["id"])


@app.delete("/api/me/avatar")
def remove_avatar(user=Depends(current_user)):
    for old in AVATARS_DIR.glob(f"{user['id']}.*"):
        old.unlink(missing_ok=True)
    with db() as conn:
        conn.execute("UPDATE user_profiles SET avatar_type=NULL WHERE user_id=?", (user["id"],))
        return user_payload(conn, user["id"])


@app.get("/api/users/{user_id}/avatar")
def user_avatar(user_id: int, user=Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT avatar_type FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row["avatar_type"]:
        fail("Avatar not found", 404)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[row["avatar_type"]]
    path = AVATARS_DIR / f"{user_id}.{ext}"
    if not path.is_file():
        fail("Avatar not found", 404)
    return FileResponse(path, media_type=row["avatar_type"], headers={"X-Content-Type-Options": "nosniff"})


@app.put("/api/me/password")
async def change_password(request: Request, user=Depends(current_user)):
    body = await request.json()
    old_password = str(body.get("current_password") or "")
    new_password = str(body.get("new_password") or "")
    if len(new_password) < 10:
        fail("New password must be at least 10 characters")
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or not await run_in_threadpool(password_ok, old_password, row["password_hash"]):
        fail("Current password is incorrect", 403)
    hashed = await run_in_threadpool(password_hash, new_password)
    with db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"]))
        current_token = hashlib.sha256(request.cookies[COOKIE_NAME].encode()).hexdigest()
        conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (user["id"], current_token))
    return {"ok": True}


def album_row(row):
    result = dict(row)
    if not result.get("release_type") and result.get("track_count") is not None:
        result["release_type"] = "single" if result["track_count"] == 1 else "album"
    result["cover_url"] = f"/api/albums/{result['id']}/cover" if result.get("cover_path") else None
    result.pop("cover_path", None)
    return result


def artist_payload(row):
    result = dict(row)
    result["portrait_url"] = (f"/api/artists/{result['id']}/image/portrait?v={result['image_version']}"
                              if result.pop("portrait_type", None) else None)
    result["banner_url"] = (f"/api/artists/{result['id']}/image/banner?v={result['image_version']}"
                            if result.pop("banner_type", None) else None)
    result.pop("image_version", None)
    return result


def track_rows(conn, user_id, where="", params=(), order="albums.artist, albums.title, tracks.disc, tracks.track_no, tracks.title", limit=500):
    query = f"""SELECT tracks.id, tracks.title, tracks.artist, tracks.album_id, albums.title AS album,
      albums.artist AS album_artist, albums.year, albums.cover_path, tracks.duration, tracks.disc,
      tracks.track_no, tracks.codec, (favorites.track_id IS NOT NULL) AS favorite
      FROM tracks JOIN albums ON albums.id=tracks.album_id
      LEFT JOIN favorites ON favorites.track_id=tracks.id AND favorites.user_id=?
      {where} ORDER BY {order} LIMIT ?"""
    rows = [dict(r) for r in conn.execute(query, (user_id, *params, limit)).fetchall()]
    for row in rows:
        row["cover_url"] = f"/api/albums/{row['album_id']}/cover" if row.pop("cover_path") else None
        row["favorite"] = bool(row["favorite"])
    return rows


@app.get("/api/home")
def home(user=Depends(current_user)):
    with db() as conn:
        albums = [album_row(r) for r in conn.execute("""SELECT albums.*,COUNT(tracks.id) AS track_count FROM albums
           JOIN tracks ON tracks.album_id=albums.id GROUP BY albums.id ORDER BY albums.created_at DESC, albums.id DESC LIMIT 12""").fetchall()]
        top_albums = [album_row(r) for r in conn.execute("""SELECT albums.*,COUNT(tracks.id) AS track_count,
           COUNT(favorites.track_id) AS liked_count FROM albums JOIN tracks ON tracks.album_id=albums.id
           LEFT JOIN favorites ON favorites.track_id=tracks.id AND favorites.user_id=?
           GROUP BY albums.id ORDER BY liked_count DESC,track_count DESC,albums.title COLLATE NOCASE LIMIT 8""",
           (user["id"],)).fetchall()]
        top_artists = [artist_payload(r) for r in conn.execute("""SELECT artist_profiles.*,
           (SELECT first_album.id FROM albums AS first_album WHERE first_album.artist_id=artist_profiles.id
            AND first_album.cover_path IS NOT NULL
            ORDER BY first_album.created_at DESC,first_album.id DESC LIMIT 1) AS cover_album_id,
           COUNT(DISTINCT albums.id) AS album_count,COUNT(tracks.id) AS track_count,
           COUNT(favorites.track_id) AS liked_count FROM artist_profiles
           JOIN albums ON albums.artist_id=artist_profiles.id JOIN tracks ON tracks.album_id=albums.id
           LEFT JOIN favorites ON favorites.track_id=tracks.id AND favorites.user_id=?
           GROUP BY artist_profiles.id ORDER BY liked_count DESC,track_count DESC,artist_profiles.name COLLATE NOCASE LIMIT 8""",
           (user["id"],)).fetchall()]
        recent = track_rows(conn, user["id"], order="tracks.created_at DESC, tracks.id DESC", limit=12)
        counts = {"tracks": conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
                  "albums": conn.execute("SELECT COUNT(*) FROM albums WHERE EXISTS(SELECT 1 FROM tracks WHERE tracks.album_id=albums.id)").fetchone()[0],
                  "artists": conn.execute("SELECT COUNT(DISTINCT artist) FROM albums WHERE EXISTS(SELECT 1 FROM tracks WHERE tracks.album_id=albums.id)").fetchone()[0]}
    return {"albums": albums, "top_albums": top_albums, "top_artists": top_artists,
            "recent": recent, "counts": counts}


@app.get("/api/tracks")
def tracks(user=Depends(current_user)):
    with db() as conn:
        return track_rows(conn, user["id"], limit=10000)


@app.get("/api/albums")
def albums(user=Depends(current_user)):
    with db() as conn:
        return [album_row(r) for r in conn.execute("""SELECT albums.*,COUNT(tracks.id) AS track_count,
          COALESCE(SUM(tracks.duration),0) AS duration FROM albums JOIN tracks ON tracks.album_id=albums.id
          GROUP BY albums.id ORDER BY albums.artist COLLATE NOCASE, albums.title COLLATE NOCASE""").fetchall()]


@app.get("/api/albums/{album_id}")
def album_detail(album_id: int, user=Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        if not row:
            fail("Album not found", 404)
        album = album_row(row)
        album["tracks"] = track_rows(conn, user["id"], "WHERE tracks.album_id=?", (album_id,),
                                     "tracks.disc, tracks.track_no, tracks.title")
    return album


@app.get("/api/artists")
def artists(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute("""SELECT p.*,COUNT(DISTINCT a.id) AS album_count,
          COUNT(t.id) AS track_count,MIN(CASE WHEN a.cover_path IS NOT NULL THEN a.id END) AS cover_album_id
          FROM artist_profiles p JOIN albums a ON a.artist_id=p.id
          JOIN tracks t ON t.album_id=a.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
        return [artist_payload(row) for row in rows]


@app.get("/api/artists/{artist_id}/image/{kind}")
def artist_image(artist_id: int, kind: str, user=Depends(current_user)):
    if kind not in ("portrait", "banner"):
        fail("Image not found", 404)
    with db() as conn:
        row = conn.execute(f"SELECT {kind}_type FROM artist_profiles WHERE id=?", (artist_id,)).fetchone()
    if not row or not row[0]:
        fail("Image not found", 404)
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[row[0]]
    path = ARTIST_IMAGES_DIR / f"artist-{artist_id}-{kind}.{extension}"
    if not path.is_file():
        fail("Image not found", 404)
    return FileResponse(path, media_type=row[0], headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/artists/{name:path}")
def artist_detail(name: str, user=Depends(current_user)):
    with db() as conn:
        profile = conn.execute("SELECT * FROM artist_profiles WHERE name=?", (name,)).fetchone()
        if not profile:
            fail("Artist not found", 404)
        rows = conn.execute("""SELECT albums.*,COUNT(tracks.id) AS track_count FROM albums
          JOIN tracks ON tracks.album_id=albums.id WHERE albums.artist_id=? GROUP BY albums.id
          ORDER BY albums.year DESC,albums.title""", (profile["id"],)).fetchall()
        result = artist_payload(profile)
        releases = [album_row(row) for row in rows]
        for release in releases:
            release["release_type"] = release["release_type"] or ("single" if release["track_count"] == 1 else "album")
        result["albums"] = [release for release in releases if release["release_type"] == "album"]
        result["singles"] = [release for release in releases if release["release_type"] == "single"]
        return result


@app.get("/api/search")
def search(q: str = "", user=Depends(current_user)):
    q = q.strip()[:100]
    if not q:
        return {"tracks": [], "albums": [], "artists": []}
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    with db() as conn:
        found_tracks = track_rows(conn, user["id"], "WHERE tracks.title LIKE ? ESCAPE '\\' OR tracks.artist LIKE ? ESCAPE '\\' OR albums.title LIKE ? ESCAPE '\\'", (like, like, like), limit=100)
        found_albums = [album_row(r) for r in conn.execute("""SELECT albums.*,COUNT(tracks.id) AS track_count FROM albums
          JOIN tracks ON tracks.album_id=albums.id WHERE albums.title LIKE ? ESCAPE '\\' OR albums.artist LIKE ? ESCAPE '\\'
          GROUP BY albums.id LIMIT 30""", (like, like)).fetchall()]
        found_artists = [r[0] for r in conn.execute("SELECT DISTINCT artist FROM albums WHERE artist LIKE ? ESCAPE '\\' LIMIT 20", (like,)).fetchall()]
    return {"tracks": found_tracks, "albums": found_albums, "artists": found_artists}


@app.get("/api/albums/{album_id}/cover")
def cover(album_id: int, user=Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT cover_path FROM albums WHERE id=?", (album_id,)).fetchone()
    if not row or not row["cover_path"]:
        fail("Cover not found", 404)
    path = (COVERS_DIR / row["cover_path"]).resolve()
    if not path.is_relative_to(COVERS_DIR) or not path.is_file():
        fail("Cover not found", 404)
    return FileResponse(path)


def allowed_music_path(path):
    resolved = Path(path).expanduser().resolve()
    return any(resolved.is_relative_to(root) for root in MUSIC_ROOTS)


def browser_mp3(track_id, source):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        fail("FFmpeg is needed to play this format; install it or use the Docker image", 503)
    stat = source.stat()
    version = hashlib.sha256(f"{source}\0{stat.st_mtime_ns}\0{stat.st_size}".encode()).hexdigest()[:16]
    target = TRANSCODES_DIR / f"track-{track_id}-{version}.mp3"
    with transcode_locks_guard:
        lock = transcode_locks.setdefault(track_id, threading.Lock())
    with lock:
        if target.is_file() and target.stat().st_size:
            return target
        temporary = TRANSCODES_DIR / f"track-{track_id}-{version}-{secrets.token_hex(4)}.tmp.mp3"
        try:
            result = subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(source), "-map", "0:a:0", "-vn", "-codec:a", "libmp3lame",
                 "-q:a", "3", "-f", "mp3", str(temporary)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=900, check=False)
            if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
                fail("This audio file could not be converted for browser playback", 422)
            os.replace(temporary, target)
            for stale in TRANSCODES_DIR.glob(f"track-{track_id}-*.mp3"):
                if stale != target:
                    stale.unlink(missing_ok=True)
            return target
        except subprocess.TimeoutExpired:
            fail("Audio conversion timed out", 504)
        finally:
            temporary.unlink(missing_ok=True)


@app.get("/api/tracks/{track_id}/stream")
def stream(track_id: int, request: Request, format: str | None = None, user=Depends(current_user)):
    if format not in (None, "mp3"):
        fail("Unsupported stream format")
    with db() as conn:
        row = conn.execute("SELECT path FROM tracks WHERE id=?", (track_id,)).fetchone()
    if not row:
        fail("Track not found", 404)
    path = Path(row["path"]).resolve()
    if not allowed_music_path(path) or not path.is_file():
        fail("Audio file unavailable", 404)
    if format == "mp3" and path.suffix.lower() != ".mp3":
        path = browser_mp3(track_id, path)
    size = path.stat().st_size
    mime = "audio/mpeg" if path.suffix.lower() == ".mp3" else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    range_header = request.headers.get("range", "")
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or (not match.group(1) and not match.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            suffix = int(match.group(2))
            start, end = max(0, size - suffix), size - 1
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
        status = 206
    else:
        start, end, status = 0, size - 1, 200
        headers["Content-Length"] = str(size)

    def chunks():
        with path.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = file.read(min(1024 * 256, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(chunks(), status_code=status, media_type=mime, headers=headers)


def parse_lrc(value):
    lines = []
    for line in (value or "").splitlines():
        matches = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        lyric = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line).strip()
        for minute, second in matches:
            lines.append({"time": round(int(minute) * 60 + float(second), 2), "text": lyric})
    return sorted(lines, key=lambda item: item["time"])[:5000]


def lyrics_payload(row):
    return {"synced": parse_lrc(row["synced"]), "plain": row["plain"] or "",
            "source": row["source"]}


def track_lyrics_row(conn, track_id):
    row = conn.execute("""SELECT tracks.path,tracks.title,tracks.artist,tracks.duration,
      albums.title AS album FROM tracks JOIN albums ON albums.id=tracks.album_id
      WHERE tracks.id=?""", (track_id,)).fetchone()
    if not row:
        fail("Track not found", 404)
    return row


@app.get("/api/tracks/{track_id}/lyrics")
def get_lyrics(track_id: int, user=Depends(current_user)):
    with db() as conn:
        track = track_lyrics_row(conn, track_id)
        sidecar = Path(track["path"]).with_suffix(".lrc")
        if allowed_music_path(sidecar) and sidecar.is_file() and sidecar.stat().st_size <= 250_000:
            text = sidecar.read_text(encoding="utf-8-sig", errors="replace")
            synced = parse_lrc(text)
            return {"synced": synced, "plain": "" if synced else text.strip(), "source": "local"}
        row = conn.execute("SELECT * FROM track_lyrics WHERE track_id=?", (track_id,)).fetchone()
        return lyrics_payload(row) if row else {"synced": [], "plain": "", "source": None}


@app.post("/api/tracks/{track_id}/lyrics/lookup")
def lookup_lyrics(track_id: int, force: bool = False, user=Depends(current_user)):
    with db() as conn:
        track = track_lyrics_row(conn, track_id)
        cached = conn.execute("SELECT * FROM track_lyrics WHERE track_id=?", (track_id,)).fetchone()
        if cached and cached["source"] != "none" and not force:
            return lyrics_payload(cached)
        if cached and int(time.time()) - cached["fetched_at"] < 7 * 86400 and not force:
            return lyrics_payload(cached)
    params = urllib.parse.urlencode({"track_name": track["title"], "artist_name": track["artist"],
                                     "album_name": track["album"], "duration": round(track["duration"])})
    req = urllib.request.Request("https://lrclib.net/api/get?" + params,
                                 headers={"User-Agent": "Current/1.0 (personal music server)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.load(response)
        synced = clean_text(result.get("syncedLyrics"), 100_000)
        plain = clean_text(result.get("plainLyrics"), 100_000)
        source = "lrclib" if synced or plain else "none"
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            fail("Lyrics service unavailable", 502)
        synced, plain, source = "", "", "none"
    except (urllib.error.URLError, ValueError, TypeError, TimeoutError):
        fail("Lyrics service unavailable", 502)
    with db() as conn:
        conn.execute("""INSERT INTO track_lyrics(track_id,synced,plain,source,fetched_at)
          VALUES(?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET
          synced=excluded.synced,plain=excluded.plain,source=excluded.source,fetched_at=excluded.fetched_at""",
                     (track_id, synced, plain, source, int(time.time())))
    return {"synced": parse_lrc(synced), "plain": plain, "source": source}


@app.put("/api/admin/tracks/{track_id}/lyrics")
async def save_lyrics(track_id: int, request: Request, user=Depends(admin)):
    content = clean_text((await request.json()).get("lyrics"), 100_000)
    if not content:
        fail("Lyrics are required")
    synced = content if parse_lrc(content) else ""
    plain = "" if synced else content
    with db() as conn:
        track_lyrics_row(conn, track_id)
        conn.execute("""INSERT INTO track_lyrics(track_id,synced,plain,source,fetched_at)
          VALUES(?,?,?,?,?) ON CONFLICT(track_id) DO UPDATE SET
          synced=excluded.synced,plain=excluded.plain,source=excluded.source,fetched_at=excluded.fetched_at""",
                     (track_id, synced, plain, "manual", int(time.time())))
    return {"synced": parse_lrc(synced), "plain": plain, "source": "manual"}


@app.post("/api/favorites/{track_id}")
def add_favorite(track_id: int, user=Depends(current_user)):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
            fail("Track not found", 404)
        conn.execute("INSERT OR IGNORE INTO favorites(user_id,track_id) VALUES(?,?)", (user["id"], track_id))
    return {"favorite": True}


@app.delete("/api/favorites/{track_id}")
def remove_favorite(track_id: int, user=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM favorites WHERE user_id=? AND track_id=?", (user["id"], track_id))
    return {"favorite": False}


@app.get("/api/favorites")
def favorites(user=Depends(current_user)):
    with db() as conn:
        return track_rows(conn, user["id"], "JOIN favorites f2 ON f2.track_id=tracks.id AND f2.user_id=?", (user["id"],), "f2.rowid DESC", 10000)


@app.get("/api/playlists")
def playlists(user=Depends(current_user)):
    with db() as conn:
        return [dict(r) for r in conn.execute("""SELECT playlists.*,COUNT(playlist_tracks.track_id) AS track_count
          FROM playlists LEFT JOIN playlist_tracks ON playlist_tracks.playlist_id=playlists.id
          WHERE playlists.user_id=? GROUP BY playlists.id ORDER BY playlists.created_at DESC, playlists.id DESC""",
          (user["id"],)).fetchall()]


@app.post("/api/playlists")
async def create_playlist(request: Request, user=Depends(current_user)):
    body = await request.json()
    name = clean_text(body.get("name"), 80)
    if not name:
        fail("Playlist name required")
    with db() as conn:
        playlist_id = conn.execute("INSERT INTO playlists(user_id,name,description) VALUES(?,?,?)",
            (user["id"], name, clean_text(body.get("description"), 300))).lastrowid
    return {"id": playlist_id, "name": name}


def own_playlist(conn, playlist_id, user_id):
    row = conn.execute("SELECT * FROM playlists WHERE id=? AND user_id=?", (playlist_id, user_id)).fetchone()
    if not row:
        fail("Playlist not found", 404)
    return row


@app.get("/api/playlists/{playlist_id}")
def playlist_detail(playlist_id: int, user=Depends(current_user)):
    with db() as conn:
        playlist = dict(own_playlist(conn, playlist_id, user["id"]))
        playlist["tracks"] = track_rows(conn, user["id"],
            "JOIN playlist_tracks pt ON pt.track_id=tracks.id AND pt.playlist_id=?", (playlist_id,), "pt.position", 10000)
    return playlist


@app.post("/api/playlists/{playlist_id}/tracks")
async def playlist_add_track(playlist_id: int, request: Request, user=Depends(current_user)):
    body = await request.json()
    try:
        track_id = int(body.get("track_id", 0))
    except (ValueError, TypeError):
        fail("Choose a song")
    with db() as conn:
        own_playlist(conn, playlist_id, user["id"])
        if not conn.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
            fail("Track not found", 404)
        position = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?", (playlist_id,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,?)", (playlist_id, track_id, position))
    return {"ok": True}


@app.delete("/api/playlists/{playlist_id}/tracks/{track_id}")
def playlist_remove_track(playlist_id: int, track_id: int, user=Depends(current_user)):
    with db() as conn:
        own_playlist(conn, playlist_id, user["id"])
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (playlist_id, track_id))
    return {"ok": True}


@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: int, user=Depends(current_user)):
    with db() as conn:
        own_playlist(conn, playlist_id, user["id"])
        conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    return {"ok": True}


@app.get("/api/admin/locations")
def locations(user=Depends(admin)):
    with db() as conn:
        return [dict(r) for r in conn.execute("""SELECT locations.*,
          (SELECT COUNT(*) FROM tracks WHERE tracks.location_id=locations.id) AS track_count
          FROM locations ORDER BY id""").fetchall()]


@app.get("/api/admin/config")
def admin_config(user=Depends(admin)):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='musicbrainz_contact'").fetchone()
    return {"musicbrainz_contact": os.getenv("MUSICBRAINZ_CONTACT", "").strip() or (row[0] if row else ""),
            "contact_from_env": bool(os.getenv("MUSICBRAINZ_CONTACT", "").strip()),
            "allowed_roots": [str(root) for root in MUSIC_ROOTS]}


@app.put("/api/admin/config")
async def update_config(request: Request, user=Depends(admin)):
    body = await request.json()
    contact = clean_text(body.get("musicbrainz_contact"), 200)
    if contact and not (re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact) or contact.startswith("https://")):
        fail("Use a contact email or HTTPS website for MusicBrainz identification")
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES('musicbrainz_contact',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (contact,))
    return {"ok": True}


@app.post("/api/admin/locations")
async def add_location(request: Request, user=Depends(admin)):
    body = await request.json()
    path = Path(clean_text(body.get("path"), 1000)).expanduser().resolve()
    if not allowed_music_path(path):
        fail("Path must be inside an allowed music mount: " + ", ".join(map(str, MUSIC_ROOTS)))
    if not path.is_dir():
        fail("Folder does not exist on the server")
    mode = body.get("mode", "tags_first")
    if mode not in ("tags_first", "folders_first"):
        fail("Invalid metadata mode")
    pattern = clean_text(body.get("pattern") or "{artist}/{album}/{track} - {title}", 300)
    with db() as conn:
        try:
            location_id = conn.execute("INSERT INTO locations(path,mode,pattern) VALUES(?,?,?)", (str(path), mode, pattern)).lastrowid
        except sqlite3.IntegrityError:
            fail("This location is already in your library", 409)
    return {"id": location_id, "path": str(path), "mode": mode, "pattern": pattern}


@app.put("/api/admin/locations/{location_id}")
async def update_location(location_id: int, request: Request, user=Depends(admin)):
    body = await request.json()
    mode = body.get("mode", "tags_first")
    if mode not in ("tags_first", "folders_first"):
        fail("Invalid metadata mode")
    pattern = clean_text(body.get("pattern") or "{artist}/{album}/{track} - {title}", 300)
    with db() as conn:
        result = conn.execute("UPDATE locations SET mode=?,pattern=?,enabled=? WHERE id=?",
            (mode, pattern, int(bool(body.get("enabled", True))), location_id))
        if not result.rowcount:
            fail("Location not found", 404)
    return {"ok": True}


@app.delete("/api/admin/locations/{location_id}")
def remove_location(location_id: int, user=Depends(admin)):
    with db() as conn:
        conn.execute("DELETE FROM locations WHERE id=?", (location_id,))
        conn.execute("DELETE FROM albums WHERE NOT EXISTS(SELECT 1 FROM tracks WHERE tracks.album_id=albums.id)")
    return {"ok": True}


def first_tag(tags, *keys):
    for key in keys:
        value = tags.get(key) if tags else None
        if value:
            return clean_text(value[0] if isinstance(value, list) else value)
    return ""


def folder_fields(path, root, pattern):
    relative = path.relative_to(root).with_suffix("").as_posix()
    fields = {}
    try:
        bits = re.split(r"(\{(?:artist|album|track|title|disc)\})", pattern)
        regex = "".join(f"(?P<{part[1:-1]}>[^/]+)" if part.startswith("{") else re.escape(part) for part in bits)
        match = re.search(r"(?:^|/)" + regex + r"$", relative, re.IGNORECASE)
        if match:
            fields = {key: value for key, value in match.groupdict().items() if value}
    except re.error:
        pass
    parts = path.relative_to(root).parts
    if len(parts) >= 2:
        fields.setdefault("album", path.parent.name)
    if len(parts) >= 3:
        fields.setdefault("artist", path.parent.parent.name)
    stem = path.stem
    num_match = re.match(r"^(\d{1,2})[ ._-]+(.+)$", stem)
    if num_match:
        fields.setdefault("track", num_match.group(1))
        fields.setdefault("title", num_match.group(2))
    elif " - " in stem:
        artist, title = stem.split(" - ", 1)
        fields.setdefault("artist", artist)
        fields.setdefault("title", title)
    else:
        fields.setdefault("title", stem)
    return fields


def extract_cover(audio):
    try:
        if hasattr(audio, "pictures") and audio.pictures:
            return audio.pictures[0].data
        if hasattr(audio, "tags") and audio.tags:
            for tag in audio.tags.values():
                if hasattr(tag, "data") and str(getattr(tag, "FrameID", "")) == "APIC":
                    return tag.data
            cover = audio.tags.get("covr")
            if cover:
                return bytes(cover[0])
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def embedded_cover(path):
    try:
        audio = MutagenFile(path)
    except Exception:
        return None
    return extract_cover(audio) if audio is not None else None


def scan_file(path, location):
    folder = folder_fields(path, Path(location["path"]), location["pattern"])
    try:
        audio = MutagenFile(path, easy=True)
        tags = audio.tags if audio is not None else None
        duration = round(float(audio.info.length), 2) if audio is not None and audio.info else 0
    except Exception:
        tags, duration = None, 0
    tagged = {
        "title": first_tag(tags, "title"), "artist": first_tag(tags, "artist"),
        "album_artist": first_tag(tags, "albumartist"),
        "album": first_tag(tags, "album"), "track": first_tag(tags, "tracknumber"),
        "disc": first_tag(tags, "discnumber"), "year": first_tag(tags, "date", "year"),
        "genre": first_tag(tags, "genre")
    }
    if location["mode"] == "folders_first":
        values = {key: folder.get(key) or tagged.get(key) for key in set(folder) | set(tagged)}
    else:
        values = {key: tagged.get(key) or folder.get(key) for key in set(folder) | set(tagged)}
    values["title"] = clean_text(values.get("title") or path.stem)
    values["artist"] = clean_text(values.get("artist") or "Unknown Artist")
    values["album_artist"] = clean_text((folder.get("artist") if location["mode"] == "folders_first" else tagged.get("album_artist")) or
                                        tagged.get("album_artist") or values["artist"])
    values["album"] = clean_text(values.get("album") or "Unknown Album")
    values["year"] = clean_text(values.get("year"), 20)[:4]
    for key in ("track", "disc"):
        match = re.match(r"\d+", str(values.get(key) or ""))
        values[key] = int(match.group()) if match else (1 if key == "disc" else 0)
    values["duration"] = duration
    return values


def save_cover(album_id, data):
    if not data or len(data) > 8_000_000:
        return None
    if data.startswith(b"\xff\xd8"):
        suffix = "jpg"
    elif data.startswith(b"\x89PNG"):
        suffix = "png"
    elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        suffix = "webp"
    else:
        return None
    filename = f"album-{album_id}.{suffix}"
    (COVERS_DIR / filename).write_bytes(data)
    for stale in COVERS_DIR.glob(f"album-{album_id}.*"):
        if stale.name != filename:
            stale.unlink(missing_ok=True)
    return filename


def index_track(path, stat, location):
    with db() as conn:
        existing = conn.execute("SELECT id,mtime,size FROM tracks WHERE path=?", (str(path),)).fetchone()
    if existing and existing["mtime"] == stat.st_mtime and existing["size"] == stat.st_size:
        return None
    values = scan_file(path, location)
    with db() as conn:
        album = conn.execute("SELECT id,cover_path FROM albums WHERE title=? AND source_artist=?",
                             (values["album"], values["album_artist"])).fetchone()
        if not album:
            # An admin may have moved another release with this title to this artist; join it rather than
            # violating UNIQUE(title, artist).
            album = conn.execute("SELECT id,cover_path FROM albums WHERE title=? AND artist=?",
                                 (values["album"], values["album_artist"])).fetchone()
        if album:
            album_id = album["id"]
            conn.execute("UPDATE albums SET year=COALESCE(NULLIF(year,''),?), genre=COALESCE(NULLIF(genre,''),?) WHERE id=?",
                         (values["year"], values["genre"], album_id))
        else:
            conn.execute("INSERT OR IGNORE INTO artist_profiles(name) VALUES(?)", (values["album_artist"],))
            artist_id = conn.execute("SELECT id FROM artist_profiles WHERE name=?", (values["album_artist"],)).fetchone()[0]
            album_id = conn.execute("""INSERT INTO albums(title,artist,source_artist,artist_id,year,genre)
              VALUES(?,?,?,?,?,?)""", (values["album"], values["album_artist"], values["album_artist"],
              artist_id, values["year"], values["genre"])).lastrowid
        if not album or not album["cover_path"]:
            image = embedded_cover(path)
            if not image:
                for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"):
                    art = path.parent / name
                    if art.is_file() and art.stat().st_size <= 8_000_000:
                        image = art.read_bytes()
                        break
            filename_cover = save_cover(album_id, image)
            if filename_cover:
                conn.execute("UPDATE albums SET cover_path=? WHERE id=?", (filename_cover, album_id))
        conn.execute("""INSERT INTO tracks(location_id,path,title,artist,album_id,disc,track_no,duration,mtime,size,codec)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
            location_id=excluded.location_id,title=excluded.title,artist=excluded.artist,
            album_id=excluded.album_id,disc=excluded.disc,track_no=excluded.track_no,
            duration=excluded.duration,mtime=excluded.mtime,size=excluded.size,codec=excluded.codec""",
            (location["id"], str(path), values["title"], values["artist"], album_id,
             values["disc"], values["track"], values["duration"], stat.st_mtime, stat.st_size, path.suffix[1:].upper()))
    return "updated" if existing else "added"


def scan_worker(enrich):
    global scan_state
    try:
        with db() as conn:
            locations = [dict(r) for r in conn.execute("SELECT * FROM locations WHERE enabled=1").fetchall()]
        scan_state.update({"phase": "scanning", "scanned": 0, "added": 0, "updated": 0, "total": 0, "message": "Reading music folders"})
        skipped = 0
        for location in locations:
            root = Path(location["path"])
            if not root.is_dir() or not allowed_music_path(root):
                continue
            seen = []
            for base, _, files in os.walk(root, followlinks=False):
                for filename in files:
                    path = Path(base) / filename
                    if path.suffix.lower() not in AUDIO_EXTENSIONS or not allowed_music_path(path):
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    seen.append(str(path))
                    scan_state["scanned"] += 1
                    scan_state["message"] = filename
                    try:
                        outcome = index_track(path, stat, location)
                    except Exception as exc:
                        skipped += 1
                        log.warning("Library scan skipped %s: %s", path, exc)
                        continue
                    if outcome:
                        scan_state[outcome] += 1
            with db() as conn:
                conn.execute("CREATE TEMP TABLE scan_seen(path TEXT PRIMARY KEY)")
                conn.executemany("INSERT OR IGNORE INTO scan_seen(path) VALUES(?)", [(p,) for p in seen])
                conn.execute("DELETE FROM tracks WHERE location_id=? AND path NOT IN (SELECT path FROM scan_seen)", (location["id"],))
        with db() as conn:
            conn.execute("DELETE FROM albums WHERE NOT EXISTS(SELECT 1 FROM tracks WHERE tracks.album_id=albums.id)")
        if enrich:
            scan_state.update({"phase": "metadata", "message": "Looking up album details"})
            with db() as conn:
                pending = [dict(r) for r in conn.execute("SELECT id,title,artist FROM albums WHERE mbid IS NULL AND title!='Unknown Album'").fetchall()]
            for album in pending:
                scan_state["message"] = f"{album['artist']} · {album['title']}"
                try:
                    matches = musicbrainz_search(album["title"], album["artist"])
                    if matches and matches[0]["score"] >= 85:
                        apply_musicbrainz(album["id"], matches[0])
                except Exception:
                    continue
        message = "Library is up to date"
        if skipped:
            message += f" · {skipped} file{'' if skipped == 1 else 's'} could not be read"
        scan_state.update({"phase": "complete", "message": message})
    except Exception as exc:
        scan_state.update({"phase": "error", "message": str(exc)})
    finally:
        scan_state["running"] = False
        scan_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        scan_lock.release()


@app.post("/api/admin/scan")
async def start_scan(request: Request, user=Depends(admin)):
    body = await request.json()
    if body.get("enrich"):
        with db() as conn:
            contact = conn.execute("SELECT value FROM settings WHERE key='musicbrainz_contact'").fetchone()
        if not (os.getenv("MUSICBRAINZ_CONTACT", "").strip() or (contact and contact[0])):
            fail("Add a MusicBrainz contact in Admin Settings before online matching")
    if not scan_lock.acquire(blocking=False):
        fail("A scan is already running", 409)
    scan_state.update({"running": True, "phase": "starting", "message": "Starting library scan"})
    threading.Thread(target=scan_worker, args=(bool(body.get("enrich", False)),), daemon=True).start()
    return dict(scan_state)


@app.get("/api/admin/scan")
def get_scan(user=Depends(admin)):
    return dict(scan_state)


def musicbrainz_request(url):
    global mb_last_call
    contact = os.getenv("MUSICBRAINZ_CONTACT", "").strip()
    if not contact:
        with db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='musicbrainz_contact'").fetchone()
            contact = row[0] if row else ""
    if not contact:
        fail("Add a MusicBrainz contact email in Admin Settings before online lookup")
    with mb_lock:
        wait = 1.1 - (time.monotonic() - mb_last_call)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": f"CurrentMusic/0.1 ({contact})", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                return json.load(response)
        finally:
            mb_last_call = time.monotonic()


def musicbrainz_search(title, artist):
    title = re.sub(r'([\\"])', r'\\\1', title)
    artist = re.sub(r'([\\"])', r'\\\1', artist)
    query = f'releasegroup:"{title}" AND artist:"{artist}"'
    url = "https://musicbrainz.org/ws/2/release-group/?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 5})
    payload = musicbrainz_request(url)
    results = []
    for item in payload.get("release-groups", []):
        artist_name = "".join(part if isinstance(part, str) else
                              (part.get("name") or part.get("artist", {}).get("name", ""))
                              for part in item.get("artist-credit", []))
        results.append({"mbid": item["id"], "title": item.get("title", ""), "artist": artist_name,
                        "year": item.get("first-release-date", "")[:4], "type": item.get("primary-type", "Album"),
                        "score": int(item.get("score", 0))})
    return results


def apply_musicbrainz(album_id, match):
    mbid = match["mbid"]
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", mbid):
        fail("Invalid MusicBrainz ID")
    genre = ""
    year = match.get("year", "")
    try:
        details = musicbrainz_request(f"https://musicbrainz.org/ws/2/release-group/{mbid}?inc=genres&fmt=json")
        year = details.get("first-release-date", "")[:4] or year
        genres = sorted(details.get("genres", []), key=lambda item: int(item.get("count") or 0), reverse=True)
        genre = ", ".join(item["name"] for item in genres[:3] if item.get("name"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    cover_name = None
    try:
        req = urllib.request.Request(f"https://coverartarchive.org/release-group/{mbid}/front-500",
                                     headers={"User-Agent": "CurrentMusic/0.1"})
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.headers.get("Content-Type", "").split(";")[0] in ("image/jpeg", "image/png", "image/webp"):
                image = response.read(8_000_001)
                cover_name = save_cover(album_id, image)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    with db() as conn:
        row = conn.execute("SELECT id FROM albums WHERE id=?", (album_id,)).fetchone()
        if not row:
            fail("Album not found", 404)
        conn.execute("UPDATE albums SET mbid=?,year=COALESCE(NULLIF(?,''),year),genre=COALESCE(NULLIF(?,''),genre),source='musicbrainz' WHERE id=?",
                     (mbid, year, genre, album_id))
        if cover_name:
            conn.execute("UPDATE albums SET cover_path=? WHERE id=?", (cover_name, album_id))
    return {"ok": True, "cover_found": bool(cover_name)}


@app.get("/api/admin/albums/{album_id}/metadata")
def metadata_matches(album_id: int, user=Depends(admin)):
    with db() as conn:
        row = conn.execute("SELECT title,artist FROM albums WHERE id=?", (album_id,)).fetchone()
    if not row:
        fail("Album not found", 404)
    try:
        return musicbrainz_search(row["title"], row["artist"])
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        fail(f"MusicBrainz lookup failed: {exc}", 502)


@app.post("/api/admin/albums/{album_id}/metadata")
async def apply_metadata(album_id: int, request: Request, user=Depends(admin)):
    body = await request.json()
    # Fetch the selected release group server-side so arbitrary client data is never trusted.
    mbid = clean_text(body.get("mbid"), 40)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", mbid):
        fail("Invalid MusicBrainz ID")
    with db() as conn:
        row = conn.execute("SELECT title,artist FROM albums WHERE id=?", (album_id,)).fetchone()
    if not row:
        fail("Album not found", 404)
    try:
        matches = musicbrainz_search(row["title"], row["artist"])
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        fail(f"MusicBrainz lookup failed: {exc}", 502)
    match = next((item for item in matches if item["mbid"] == mbid), None)
    if not match:
        fail("Match no longer available", 404)
    return apply_musicbrainz(album_id, match)


@app.get("/api/admin/users")
def list_users(user=Depends(admin)):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT id,username,role,created_at FROM users ORDER BY id").fetchall()]


@app.post("/api/admin/users")
async def create_user(request: Request, user=Depends(admin)):
    body = await request.json()
    username = clean_text(body.get("username"), 40)
    password = str(body.get("password") or "")
    role = body.get("role", "user")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username) or role not in ("admin", "user") or len(password) < 10:
        fail("Use a valid username, role, and password of at least 10 characters")
    hashed = await run_in_threadpool(password_hash, password)
    with db() as conn:
        try:
            user_id = conn.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (username, hashed, role)).lastrowid
            conn.execute("INSERT INTO user_profiles(user_id,display_name) VALUES(?,?)", (user_id, username))
        except sqlite3.IntegrityError:
            fail("Username already exists", 409)
    return {"id": user_id, "username": username, "role": role}


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, user=Depends(admin)):
    if user_id == user["id"]:
        fail("You cannot delete your own account")
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    for old in AVATARS_DIR.glob(f"{user_id}.*"):
        old.unlink(missing_ok=True)
    return {"ok": True}


def public_https_url(value):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        fail("Use a public HTTPS feed URL")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            fail("Feed address must be public")
    except socket.gaierror:
        fail("Feed address could not be resolved")
    return parsed.geturl()


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_podcast_feed(url):
    public_https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Current/1.0 (personal podcast reader)",
                                                   "Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        with urllib.request.build_opener(SafeRedirect).open(request, timeout=12) as response:
            if int(response.headers.get("Content-Length", "0")) > 5_000_000:
                fail("Podcast feed is too large")
            content = response.read(5_000_001)
            if len(content) > 5_000_000:
                fail("Podcast feed is too large")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        fail(f"Could not load podcast feed: {exc}", 502)
    return parse_podcast_feed(content)


def plain_description(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:2000]


def podcast_duration(value):
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    if len(parts) in (2, 3) and all(part.isdigit() for part in parts):
        result = 0
        for part in parts:
            result = result * 60 + int(part)
        return result
    return 0


def parse_podcast_feed(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        fail("Unsupported podcast feed")
    try:
        channel = ET.fromstring(content).find("channel")
    except ET.ParseError:
        fail("Invalid podcast RSS feed")
    if channel is None:
        fail("Podcast feed needs an RSS channel")
    ns = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    title = clean_text(channel.findtext("title"), 200)
    if not title:
        fail("Podcast feed has no title")
    image = channel.find(ns + "image")
    artwork = image.get("href") if image is not None else channel.findtext("image/url")
    artwork = artwork if artwork and urllib.parse.urlparse(artwork).scheme == "https" else None
    podcast = {"title": title, "author": clean_text(channel.findtext(ns + "author"), 200),
               "description": plain_description(channel.findtext("description")),
               "artwork_url": artwork,
               "website_url": channel.findtext("link") if (channel.findtext("link") or "").startswith("https://") else None}
    episodes = []
    for item in channel.findall("item")[:300]:
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        audio_url = enclosure.get("url", "")
        if urllib.parse.urlparse(audio_url).scheme != "https":
            continue
        episode_image = item.find(ns + "image")
        episode_art = episode_image.get("href") if episode_image is not None else None
        if episode_art and urllib.parse.urlparse(episode_art).scheme != "https":
            episode_art = None
        raw_date = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(raw_date).astimezone(timezone.utc).isoformat() if raw_date else None
        except (ValueError, TypeError, IndexError):
            published = None
        episodes.append({"guid": clean_text(item.findtext("guid") or audio_url, 1000),
                         "title": clean_text(item.findtext("title"), 300) or "Untitled episode",
                         "description": plain_description(item.findtext("description") or item.findtext(ns + "summary")),
                         "published_at": published,
                         "duration": podcast_duration(item.findtext(ns + "duration")),
                         "audio_url": audio_url,
                         "artwork_url": episode_art})
    return podcast, episodes


def save_podcast(conn, feed_url, podcast, episodes):
    conn.execute("""INSERT INTO podcasts(feed_url,title,author,description,artwork_url,website_url)
      VALUES(?,?,?,?,?,?) ON CONFLICT(feed_url) DO UPDATE SET title=excluded.title,author=excluded.author,
      description=excluded.description,artwork_url=excluded.artwork_url,website_url=excluded.website_url,
      refreshed_at=CURRENT_TIMESTAMP""",
                 (feed_url, podcast["title"], podcast["author"], podcast["description"],
                  podcast["artwork_url"], podcast["website_url"]))
    podcast_id = conn.execute("SELECT id FROM podcasts WHERE feed_url=?", (feed_url,)).fetchone()[0]
    for episode in episodes:
        conn.execute("""INSERT INTO podcast_episodes(podcast_id,guid,title,description,published_at,duration,audio_url,artwork_url)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(podcast_id,guid) DO UPDATE SET
          title=excluded.title,description=excluded.description,published_at=excluded.published_at,
          duration=excluded.duration,audio_url=excluded.audio_url,artwork_url=excluded.artwork_url""",
                     (podcast_id, episode["guid"], episode["title"], episode["description"],
                      episode["published_at"], episode["duration"], episode["audio_url"], episode["artwork_url"]))
    return podcast_id


def subscribed_podcast(conn, podcast_id, user_id):
    row = conn.execute("""SELECT podcasts.* FROM podcasts JOIN podcast_subscriptions s ON s.podcast_id=podcasts.id
      WHERE podcasts.id=? AND s.user_id=?""", (podcast_id, user_id)).fetchone()
    if not row:
        fail("Podcast not found in your subscriptions", 404)
    return dict(row)


@app.get("/api/admin/artists")
def admin_artists(user=Depends(admin)):
    with db() as conn:
        rows = conn.execute("""SELECT p.*,COUNT(DISTINCT a.id) AS album_count,COUNT(t.id) AS track_count
          FROM artist_profiles p LEFT JOIN albums a ON a.artist_id=p.id
          LEFT JOIN tracks t ON t.album_id=a.id GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
        return [artist_payload(row) for row in rows]


@app.post("/api/admin/artists")
async def create_artist_profile(request: Request, user=Depends(admin)):
    name = clean_text((await request.json()).get("name"), 200)
    if not name:
        fail("Artist name is required")
    with db() as conn:
        try:
            artist_id = conn.execute("INSERT INTO artist_profiles(name) VALUES(?)", (name,)).lastrowid
        except sqlite3.IntegrityError:
            fail("Artist already exists", 409)
        return artist_payload(conn.execute("SELECT * FROM artist_profiles WHERE id=?", (artist_id,)).fetchone())


@app.get("/api/admin/artists/{artist_id}")
def admin_artist_detail(artist_id: int, user=Depends(admin)):
    with db() as conn:
        row = conn.execute("SELECT * FROM artist_profiles WHERE id=?", (artist_id,)).fetchone()
        if not row:
            fail("Artist not found", 404)
        result = artist_payload(row)
        result["albums"] = [album_row(r) for r in conn.execute("""SELECT albums.*,
          COUNT(tracks.id) AS track_count FROM albums LEFT JOIN tracks ON tracks.album_id=albums.id
          WHERE albums.artist_id=? GROUP BY albums.id ORDER BY albums.title""", (artist_id,))]
        return result


@app.put("/api/admin/artists/{artist_id}")
async def update_artist_profile(artist_id: int, request: Request, user=Depends(admin)):
    body = await request.json()
    name = clean_text(body.get("name"), 200)
    bio = clean_text(body.get("bio"), 2000)
    if not name:
        fail("Artist name is required")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM artist_profiles WHERE id=?", (artist_id,)).fetchone():
            fail("Artist not found", 404)
        try:
            conn.execute("UPDATE artist_profiles SET name=?,bio=? WHERE id=?", (name, bio, artist_id))
            conn.execute("UPDATE albums SET artist=? WHERE artist_id=?", (name, artist_id))
        except sqlite3.IntegrityError:
            fail("That artist name conflicts with another artist or album", 409)
        return artist_payload(conn.execute("SELECT * FROM artist_profiles WHERE id=?", (artist_id,)).fetchone())


@app.put("/api/admin/artists/{artist_id}/image/{kind}")
async def upload_artist_image(artist_id: int, kind: str, request: Request, user=Depends(admin)):
    if kind not in ("portrait", "banner"):
        fail("Choose portrait or banner")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > 6_000_000:
        fail("Choose an image under 6 MB")
    data = await request.body()
    if not data or len(data) > 6_000_000:
        fail("Choose an image under 6 MB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        image_type, extension = "image/png", "png"
    elif data.startswith(b"\xff\xd8\xff"):
        image_type, extension = "image/jpeg", "jpg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image_type, extension = "image/webp", "webp"
    else:
        fail("Choose a PNG, JPEG or WebP image")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM artist_profiles WHERE id=?", (artist_id,)).fetchone():
            fail("Artist not found", 404)
    path = ARTIST_IMAGES_DIR / f"artist-{artist_id}-{kind}.{extension}"
    temp = ARTIST_IMAGES_DIR / f".{artist_id}-{kind}-{secrets.token_hex(8)}.tmp"
    temp.write_bytes(data)
    temp.replace(path)
    for old in ARTIST_IMAGES_DIR.glob(f"artist-{artist_id}-{kind}.*"):
        if old != path:
            old.unlink(missing_ok=True)
    with db() as conn:
        conn.execute(f"UPDATE artist_profiles SET {kind}_type=?,image_version=? WHERE id=?",
                     (image_type, int(time.time() * 1000), artist_id))
        return artist_payload(conn.execute("SELECT * FROM artist_profiles WHERE id=?", (artist_id,)).fetchone())


@app.delete("/api/admin/artists/{artist_id}/image/{kind}")
def delete_artist_image(artist_id: int, kind: str, user=Depends(admin)):
    if kind not in ("portrait", "banner"):
        fail("Choose portrait or banner")
    for old in ARTIST_IMAGES_DIR.glob(f"artist-{artist_id}-{kind}.*"):
        old.unlink(missing_ok=True)
    with db() as conn:
        conn.execute(f"UPDATE artist_profiles SET {kind}_type=NULL WHERE id=?", (artist_id,))
        row = conn.execute("SELECT * FROM artist_profiles WHERE id=?", (artist_id,)).fetchone()
        if not row:
            fail("Artist not found", 404)
        return artist_payload(row)


@app.put("/api/admin/albums/{album_id}/artist")
async def assign_album_artist(album_id: int, request: Request, user=Depends(admin)):
    body = await request.json()
    try:
        artist_id = int(body.get("artist_id"))
    except (ValueError, TypeError):
        fail("Choose an artist")
    release_type = body.get("release_type")
    if release_type not in ("album", "single"):
        fail("Choose album or single")
    with db() as conn:
        artist = conn.execute("SELECT name FROM artist_profiles WHERE id=?", (artist_id,)).fetchone()
        if not artist:
            fail("Artist not found", 404)
        if not conn.execute("SELECT 1 FROM albums WHERE id=?", (album_id,)).fetchone():
            fail("Album not found", 404)
        try:
            conn.execute("UPDATE albums SET artist_id=?,artist=?,release_type=? WHERE id=?",
                         (artist_id, artist["name"], release_type, album_id))
        except sqlite3.IntegrityError:
            fail("This artist already has a release with that title", 409)
        return album_row(conn.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone())


@app.get("/api/podcasts")
def list_podcasts(user=Depends(current_user)):
    with db() as conn:
        return [dict(row) for row in conn.execute("""SELECT podcasts.*,
          (SELECT COUNT(*) FROM podcast_episodes WHERE podcast_id=podcasts.id) AS episode_count
          FROM podcasts JOIN podcast_subscriptions s ON s.podcast_id=podcasts.id
          WHERE s.user_id=? ORDER BY podcasts.title COLLATE NOCASE""", (user["id"],))]


@app.post("/api/podcasts")
async def subscribe_podcast(request: Request, user=Depends(current_user)):
    feed_url = clean_text((await request.json()).get("feed_url"), 2000)
    public_https_url(feed_url)
    podcast, episodes = fetch_podcast_feed(feed_url)
    with db() as conn:
        podcast_id = save_podcast(conn, feed_url, podcast, episodes)
        conn.execute("INSERT OR IGNORE INTO podcast_subscriptions(user_id,podcast_id) VALUES(?,?)",
                     (user["id"], podcast_id))
    return {"id": podcast_id, "title": podcast["title"], "episode_count": len(episodes)}


@app.get("/api/podcasts/episodes/recent")
def recent_episodes(user=Depends(current_user)):
    with db() as conn:
        return [dict(row) for row in conn.execute("""SELECT e.*,p.title AS podcast_title,
          COALESCE(e.artwork_url,p.artwork_url) AS cover_url,COALESCE(progress.position,0) AS position
          FROM podcast_episodes e JOIN podcasts p ON p.id=e.podcast_id
          JOIN podcast_subscriptions s ON s.podcast_id=p.id AND s.user_id=?
          LEFT JOIN episode_progress progress ON progress.episode_id=e.id AND progress.user_id=?
          ORDER BY e.published_at DESC,e.id DESC LIMIT 50""", (user["id"], user["id"]))]


@app.get("/api/podcasts/{podcast_id}")
def podcast_detail(podcast_id: int, user=Depends(current_user)):
    with db() as conn:
        podcast = subscribed_podcast(conn, podcast_id, user["id"])
        podcast["episodes"] = [dict(row) for row in conn.execute("""SELECT e.*,
          COALESCE(e.artwork_url,?) AS cover_url,COALESCE(progress.position,0) AS position
          FROM podcast_episodes e LEFT JOIN episode_progress progress
          ON progress.episode_id=e.id AND progress.user_id=?
          WHERE e.podcast_id=? ORDER BY e.published_at DESC,e.id DESC LIMIT 300""",
          (podcast["artwork_url"], user["id"], podcast_id))]
        return podcast


@app.post("/api/podcasts/{podcast_id}/refresh")
def refresh_podcast(podcast_id: int, user=Depends(current_user)):
    with db() as conn:
        podcast = subscribed_podcast(conn, podcast_id, user["id"])
    data, episodes = fetch_podcast_feed(podcast["feed_url"])
    with db() as conn:
        save_podcast(conn, podcast["feed_url"], data, episodes)
    return {"ok": True, "episode_count": len(episodes)}


@app.delete("/api/podcasts/{podcast_id}/subscription")
def unsubscribe_podcast(podcast_id: int, user=Depends(current_user)):
    with db() as conn:
        conn.execute("DELETE FROM podcast_subscriptions WHERE user_id=? AND podcast_id=?",
                     (user["id"], podcast_id))
    return {"ok": True}


@app.put("/api/podcasts/episodes/{episode_id}/progress")
async def save_episode_progress(episode_id: int, request: Request, user=Depends(current_user)):
    try:
        position = max(0, min(float((await request.json()).get("position", 0)), 1_000_000))
    except (ValueError, TypeError):
        fail("Invalid playback position")
    with db() as conn:
        row = conn.execute("""SELECT e.duration FROM podcast_episodes e JOIN podcast_subscriptions s
          ON s.podcast_id=e.podcast_id WHERE e.id=? AND s.user_id=?""", (episode_id, user["id"])).fetchone()
        if not row:
            fail("Episode not found", 404)
        completed = int(bool(row["duration"] and position >= row["duration"] - 30))
        conn.execute("""INSERT INTO episode_progress(user_id,episode_id,position,completed)
          VALUES(?,?,?,?) ON CONFLICT(user_id,episode_id) DO UPDATE SET
          position=excluded.position,completed=excluded.completed,updated_at=CURRENT_TIMESTAMP""",
          (user["id"], episode_id, position, completed))
    return {"position": position, "completed": bool(completed)}


# Compress only the web assets; audio streams use byte ranges and must stay untouched.
app.mount("/", GZipMiddleware(StaticFiles(directory=APP_DIR / "web", html=True), minimum_size=1024, compresslevel=6), name="web")
