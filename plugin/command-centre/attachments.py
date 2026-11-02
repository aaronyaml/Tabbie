"""Attachment pipeline.

Every attachment becomes an immutable original inside the request folder, with a manifest
entry (name, extension, MIME, size, sha256, source path). Derived representations (extracted
ZIPs, PDF text, media metadata) are written beside it and never replace the original.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from .common import read_json, write_json

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
ARCHIVE_EXT = {".zip"}
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".html", ".css",
            ".js", ".jsx", ".ts", ".tsx", ".py", ".rs", ".go", ".swift", ".kt", ".java", ".sql",
            ".sh", ".toml", ".ini", ".svg"}

# @file:path, @file:"quoted path", @file:'quoted', optional :line-range suffix.
_FILE_REF = re.compile(r"""@file:(?:"([^"]+)"|'([^']+)'|`([^`]+)`|(\S+))""")
# Bare absolute or ~ paths a user pasted/dragged into the message.
_BARE_PATH = re.compile(r"""(?<![\w@:])((?:~|/(?:Users|private|tmp|Volumes|var/folders))/[^\s"'`<>|]+"""
                        r"""|(?<![\w])[A-Za-z]:\\(?:Users|Temp|tmp)\\[^\s"'`<>|]+)""")  # + Windows C:\Users\..

_SECRET_HINTS = (".ssh/", ".aws/", ".gnupg/", "/.env", "credentials", "auth.json", ".netrc",
                 "mcp-tokens", "keychain", "/.codex/", "/.claude/.credentials", "/.config/gh/",
                 "/.docker/config.json", "/.kube/")
# Private keys and tokens by file name, wherever they live.
_SECRET_NAME = re.compile(r"(^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|\.(pem|key|p12|pfx|token|keystore|jks)$)", re.I)
MAX_FILES = 40  # per message; a pasted directory listing must not copy a whole tree


def _is_secret(path: str) -> bool:
    low = path.replace("\\", "/").lower()  # Windows paths: C:\Users\me\.ssh\id_rsa
    if any(h in low for h in _SECRET_HINTS) or _SECRET_NAME.search(Path(path).name):
        return True
    # Tabbie's own state (sign-in, sidecar token, sessions) never goes to a worker; WebUI uploads do.
    return "/.hermes/" in low and "/.hermes/webui/attachments/" not in low


def kind_of(ext: str, mime: str | None) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXT or (mime or "").startswith("image/"):
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in VIDEO_EXT or (mime or "").startswith("video/"):
        return "video"
    if ext in AUDIO_EXT or (mime or "").startswith("audio/"):
        return "audio"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in TEXT_EXT or (mime or "").startswith("text/"):
        return "text"
    return "other"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_line_range(raw: str) -> str:
    return re.sub(r":\d+(-\d+)?$", "", raw)


def find_candidates(message: Any) -> tuple[list[Path], list[tuple[bytes, str]]]:
    """Return (file paths referenced in the message, inline images as (bytes, mime))."""
    texts: list[str] = []
    inline: list[tuple[bytes, str]] = []
    if isinstance(message, str):
        texts.append(message)
    elif isinstance(message, list):
        for part in message:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")
            if ptype in ("text", "input_text"):
                texts.append(part.get("text", ""))
            elif ptype in ("image_url", "input_image"):
                url = part.get("image_url")
                url = url.get("url") if isinstance(url, dict) else (url or part.get("url", ""))
                m = re.match(r"^data:([^;,]+);base64,(.*)$", url or "", re.S)
                if m:
                    try:
                        inline.append((base64.b64decode(m.group(2)), m.group(1)))
                    except Exception:
                        pass
                elif url and url.startswith("file://"):
                    texts.append(url[len("file://"):])
    paths: list[Path] = []
    seen: set[str] = set()
    for text in texts:
        raws = [next(g for g in m.groups() if g) for m in _FILE_REF.finditer(text)]
        raws += [m.group(1).rstrip(".,;:)]}") for m in _BARE_PATH.finditer(text)]
        for raw in raws:
            p = Path(_strip_line_range(raw)).expanduser()
            try:
                p = p.resolve()
            except OSError:
                continue
            key = str(p)
            if key in seen or not p.is_file():
                continue
            if _is_secret(key):
                continue  # never ingest credential files, even if referenced
            seen.add(key)
            paths.append(p)
            if len(paths) >= MAX_FILES:
                return paths, inline
    return paths, inline


class Manifest:
    def __init__(self, request_dir: Path):
        self.dir = request_dir / "attachments"
        self.originals = self.dir / "originals"
        self.derived = self.dir / "derived"
        self.path = self.dir / "manifest.json"
        self.data = read_json(self.path, {"attachments": []})

    @property
    def items(self) -> list[dict]:
        return self.data["attachments"]

    def save(self) -> None:
        write_json(self.path, self.data)

    def _store(self, src_bytes_or_path, name: str, source: str, original_path: str | None,
               cfg: dict) -> dict | None:
        self.originals.mkdir(parents=True, exist_ok=True)
        idx = len(self.items) + 1
        safe = re.sub(r"[^\w.\- ]+", "_", name).strip() or f"attachment-{idx}"
        target = self.originals / f"{idx:02d}-{safe}"
        if isinstance(src_bytes_or_path, Path):
            max_mb = float(cfg.get("max_file_mb", 500))
            size = src_bytes_or_path.stat().st_size
            total = sum(e.get("size_bytes", 0) for e in self.items)
            if size > max_mb * 1024 * 1024 or total + size > float(cfg.get("max_total_mb", 1000)) * 1024 * 1024:
                return None
            shutil.copy2(src_bytes_or_path, target)
        else:
            target.write_bytes(src_bytes_or_path)
        target.chmod(0o444)  # originals are read-only so no worker mutates them by accident
        digest = _sha256(target)
        for existing in self.items:  # same content already attached to this request
            if existing["sha256"] == digest:
                target.chmod(0o644)
                target.unlink()
                return None
        ext = target.suffix.lower()
        mime = mimetypes.guess_type(target.name)[0]
        entry = {
            "index": idx,
            "original_name": name,
            "original_path": original_path,
            "stored_path": str(target),
            "extension": ext,
            "mime": mime or "application/octet-stream",
            "kind": kind_of(ext, mime),
            "size_bytes": target.stat().st_size,
            "sha256": digest,
            "source": source,
            "derived": [],
        }
        self.items.append(entry)
        self._derive(entry, cfg)
        return entry

    def ingest(self, message: Any, cfg: dict) -> list[dict]:
        paths, inline = find_candidates(message)
        added: list[dict] = []
        for p in paths:
            e = self._store(p, p.name, "path-in-message", str(p), cfg)
            if e:
                added.append(e)
        for n, (blob, mime) in enumerate(inline, 1):
            ext = mimetypes.guess_extension(mime) or ".bin"
            e = self._store(blob, f"inline-image-{n}{ext}", "inline-image", None, cfg)
            if e:
                added.append(e)
        if added:
            self.save()
        return added

    # -- derived representations (never replace originals) --------------------------------
    def _derive(self, entry: dict, cfg: dict) -> None:
        src = Path(entry["stored_path"])
        out = self.derived / src.stem
        try:
            if entry["kind"] == "archive":
                self._safe_unzip(src, out, cfg)
                entry["derived"].append({"type": "extracted-archive", "path": str(out)})
            elif entry["kind"] == "pdf" and shutil.which("pdftotext"):
                out.parent.mkdir(parents=True, exist_ok=True)
                txt = out.with_suffix(".txt")
                subprocess.run(["pdftotext", "-layout", str(src), str(txt)], timeout=60,
                               check=True, capture_output=True)
                entry["derived"].append({"type": "pdf-text", "path": str(txt)})
            elif entry["kind"] in ("video", "audio") and shutil.which("ffprobe"):
                out.parent.mkdir(parents=True, exist_ok=True)
                meta = out.with_suffix(".probe.json")
                r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                                    "-show_format", "-show_streams", str(src)],
                                   timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if r.returncode == 0:
                    meta.write_text(r.stdout, encoding="utf-8")
                    entry["derived"].append({"type": "media-metadata", "path": str(meta)})
        except Exception as exc:  # derivation is best-effort; the original is what matters
            entry["derived"].append({"type": "error", "detail": str(exc)[:200]})

    @staticmethod
    def _safe_unzip(src: Path, out: Path, cfg: dict) -> None:
        max_total = float(cfg.get("zip_max_total_mb", 1000)) * 1024 * 1024
        max_files = int(cfg.get("zip_max_files", 5000))
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
            if len(infos) > max_files or sum(i.file_size for i in infos) > max_total:
                raise ValueError("archive exceeds extraction limits; original kept unextracted")
            out.mkdir(parents=True, exist_ok=True)
            root = out.resolve()
            for info in infos:
                dest = (out / info.filename).resolve()
                if not dest.is_relative_to(root):  # also catches C:\ and \\server names on Windows
                    raise ValueError(f"unsafe path in archive: {info.filename}")
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as fsrc, dest.open("wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)

    def render(self) -> str:
        """Human/agent-readable manifest block passed to every worker."""
        if not self.items:
            return "ATTACHMENTS: none"
        lines = [f"ATTACHMENTS ({len(self.items)}) — originals are byte-exact copies; "
                 f"manifest: {self.path}"]
        for e in self.items:
            lines.append(
                f"  [{e['index']}] {e['original_name']} | {e['kind']} | {e['mime']} | "
                f"{e['size_bytes']} bytes | sha256 {e['sha256'][:16]}…\n"
                f"      original: {e['stored_path']}"
                + (f"\n      came from: {e['original_path']}" if e.get("original_path") else "")
            )
            for d in e.get("derived", []):
                if d.get("path"):
                    lines.append(f"      derived ({d['type']}, supplementary only): {d['path']}")
        return "\n".join(lines)

    def images(self) -> list[str]:
        return [e["stored_path"] for e in self.items
                if e["kind"] == "image" and e["extension"] in {".png", ".jpg", ".jpeg", ".webp", ".gif"}]
