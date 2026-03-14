# Music Server Overhaul Design

**Date:** 2026-03-14
**Status:** Approved

## Summary

Overhaul the PyPlanet Music Server app from a basic .ogg rotation player into a fully configurable music hub with YouTube integration (yt-dlp + ffmpeg), vote-skip, shuffle, per-player favorites, and an embedded HTTP file server.

## Design Principles

1. **Fully configurable** - every value via PyPlanet app settings, nothing hardcoded
2. **Graceful degradation** - auto-detect yt-dlp/ffmpeg; features disable if missing
3. **Backwards compatible** - existing SONGS config and commands still work identically
4. **Reusable** - any PyPlanet deployment can adopt this with zero code changes

## Architecture

```
+-----------------------------------------------------------+
|                    Music Server App                         |
|                                                            |
|  +---------------+  +--------------+  +------------------+ |
|  | Core Engine   |  | HTTP Server  |  | YT Downloader    | |
|  | (always on)   |  | (opt-in)     |  | (auto-detect)    | |
|  |               |  |              |  |                  | |
|  | - rotation    |  | - aiohttp    |  | - yt-dlp         | |
|  | - queue       |  | - serves .ogg|  | - ffmpeg         | |
|  | - shuffle     |  | - config port|  | - size/dur cap   | |
|  | - metadata    |  | - public URL |  | - auto-cleanup   | |
|  +---------------+  +--------------+  +------------------+ |
|  +---------------+  +--------------+                       |
|  | Favorites     |  | Vote-Skip    |                       |
|  | (DB model)    |  | (threshold)  |                       |
|  +---------------+  +--------------+                       |
+-----------------------------------------------------------+
```

## Phase 1 - Bug Fixes

- Fix `insert_song()` player reference inconsistency (pass player object, not nickname)
- Remove unused `re` import
- Add try/except around `TinyTag.get()` with graceful fallback metadata
- Parallel metadata loading via `asyncio.gather()` at startup

## Phase 2 - Core Features

### Settings (all via `//settings`)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| override_map_music | bool | True | Force music over map's built-in |
| shuffle_enabled | bool | False | Shuffle rotation |
| skip_vote_enabled | bool | True | Allow vote-skip |
| skip_vote_threshold | float | 0.5 | Fraction of players needed to skip |
| skip_vote_timeout | int | 30 | Vote expiry in seconds |
| favorites_enabled | bool | True | Enable favorites system |
| http_enabled | bool | False | Enable HTTP file server |
| http_host | str | 0.0.0.0 | HTTP server bind address |
| http_port | int | 8080 | HTTP server bind port |
| http_public_url | str | (empty) | Public URL prefix for served files |
| yt_enabled | bool | True | Enable YouTube integration |
| yt_dlp_path | str | yt-dlp | Path to yt-dlp binary |
| ffmpeg_path | str | ffmpeg | Path to ffmpeg binary |
| yt_download_dir | str | ./music | Download directory |
| yt_max_duration | int | 600 | Max song duration in seconds |
| yt_max_filesize | str | 50M | Max file size |
| yt_cleanup_after_days | int | 7 | Auto-cleanup after N days (0=never) |

### Commands

| Command | Permission | Description |
|---------|-----------|-------------|
| /song | All | Show current song |
| /songlist (/musiclist) | All | Browse library |
| /playlist | All | View queue |
| /skipmusic | All | Vote to skip current song |
| /fav | All | Favorite current song |
| /unfav | All | Remove current from favorites |
| /favlist | All | Browse personal favorites (click to queue) |
| //shuffle | Admin | Toggle shuffle mode |
| //ytplay url | Admin | Download YouTube audio and queue |
| //clearplaylist | Admin | Clear queue |
| //topfavs | Admin | Server-wide favorite rankings |

### Database Model

```python
class PlayerFavorite(Model):
    player = ForeignKeyField(Player)
    song_url = CharField(max_length=512)
    song_title = CharField(default='Unknown')
    song_artist = CharField(default='Unknown')
    created_at = DateTimeField(default=datetime.now)
```

### YouTube Pipeline

1. Admin runs `//ytplay <url>`
2. yt-dlp extracts best audio
3. ffmpeg converts to .ogg Vorbis
4. File saved to yt_download_dir
5. HTTP server serves file at http_public_url/filename.ogg
6. Song added to queue, plays on next map

### Vote-Skip System

- `/skipmusic` starts or joins a vote
- Configurable threshold (default 50% of online players)
- 30-second timeout
- One vote per player per song
- On success: immediately plays next song via SetForcedMusic

### Shuffle

- `//shuffle` toggles between sequential and random
- Fisher-Yates shuffle: plays all songs before repeating
- Playlist queue items always play in FIFO order regardless of shuffle

### Startup Flow

1. Load songs from settings (parallel metadata extraction)
2. Check yt_enabled -> probe yt-dlp/ffmpeg -> enable/disable + log
3. Check http_enabled -> start aiohttp server -> log bind address
4. Check favorites_enabled -> register favorite commands
5. Check skip_vote_enabled -> register skip command
6. Register core commands

## Phase 3 - UI Improvements

- Now Playing indicator in songlist
- Favorite count badge per song
- Source icon (config vs YouTube) in views
- Shuffle status in /song display
- "Requested by" with player nickname in playlist view

## Graceful Degradation

On startup, the app probes for external dependencies:
- If yt-dlp missing: YouTube commands not registered, log warning
- If ffmpeg missing: YouTube commands not registered, log warning
- If http_enabled=False: no HTTP server started, only pre-configured URLs work
- If favorites_enabled=False: favorite commands not registered
- If skip_vote_enabled=False: skip command not registered
- Base jukebox (rotation, queue, songlist) always works
