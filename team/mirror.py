"""Mirror shared Tabbie sessions to the relay, live (Phase 2). Messages sent from the team site are run by
runner.py (Phase 3), which binds the chat it runs them in to the shared chat's room; this file then streams it.

Reads only. Never writes into Tabbie's files and needs no WebUI login:
  - ~/.hermes/webui/sessions/<sid>.json       messages (whole-file rewrites; we diff by message id)
  - ~/.hermes/webui/sessions/_turn_journal/   turn start / end (append-only JSONL)
  - ~/.hermes/webui/sessions/_run_journal/    streaming tokens for direct runs (append-only JSONL)
  - sidecar GET /draft and /session           a hub worker's live draft and narration lines (token header)

Which sessions are shared: those in a shared project (config "projects", none by default), plus any
session id listed in "sessions". Each Tabbie session maps to a random room id ("s-…"), so the relay never sees
Tabbie's ids. Everything sent is signed by this Mac and encrypted with the team key (ttcore.seal).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
import urllib.request
from pathlib import Path

import ttcore as C

WEBUI = Path(os.environ.get("TT_WEBUI_STATE", Path.home() / ".hermes" / "webui"))
SESS = WEBUI / "sessions"
SIDECAR = os.environ.get("TT_SIDECAR", "http://127.0.0.1:8794")
TICK = 0.15  # seconds between file checks: well under the 300 ms target
MAX_TEXT = 200_000
# Files: every file a chat attaches (your uploads, and files a reply hands over with MEDIA:) goes to the team,
# encrypted like everything else, in chunks that fit the relay's event size. Paths merely mentioned in text never
# do, and neither do key and secret files, wherever a reply points.
ATTACH = WEBUI / "attachments"
MAX_FILE = 25 * 1024 * 1024
_HOME = Path.home()
SECRET_DIRS = [_HOME / x for x in (".ssh", ".gnupg", ".aws", ".hermes/team", ".config/op", ".config/gh", "Library/Keychains",
                                    "Library/Application Support/1Password", ".docker", ".kube")]
SECRET_NAME = re.compile(r"^(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_[a-z0-9]+(\.pub)?|.*\.(pem|key|p12|pfx|keychain(-db)?|kdbx|ovpn))$", re.I)
CHUNK = 96 * 1024
ATTACHED_RX = re.compile(r"\n*\[Attached files: ([^\]]+)\]")
UPLOADED_RX = re.compile(r"I've uploaded \d+ file\(s\): (.+)")
MEDIA_RX = re.compile(r"^[ \t]*MEDIA:[ \t]*(/\S+)[ \t]*$", re.M)


def motion_root() -> Path | None:
    """Tabbie's Motion studio folder (config/motion.yaml `studio:`, default motion/ in Tabbie). Its chats are
    never shared: Motion stays on this Mac."""
    cfg = Path(__file__).resolve().parents[1] / "config" / "motion.yaml"
    root = None
    try:
        m = re.search(r"^studio:\s*(\S.*?)\s*$", cfg.read_text(encoding="utf-8"), re.M)
        root = m and m.group(1).strip("'\"")
    except OSError:
        pass
    p = Path(os.path.expanduser(root)) if root else Path(__file__).resolve().parents[1] / "motion"
    try:
        return p.resolve()
    except OSError:
        return None


def is_motion(data: dict, root: Path | None) -> bool:
    for k in ("workspace", "created_workspace"):
        w = data.get(k)
        if root and isinstance(w, str) and w:
            try:
                if Path(os.path.expanduser(w)).resolve().is_relative_to(root):
                    return True
            except (OSError, ValueError):
                pass
    for m in data.get("messages") or []:
        if m.get("role") == "user":
            return text_of(m.get("content")).lstrip().startswith("/motion")
    return False


def split_files(m: dict, text: str) -> tuple[str, list[str]]:
    """The message text without Tabbie's file markers, and the file paths it carries."""
    paths: list[str] = []
    for a in m.get("attachments") or []:
        if isinstance(a, dict) and isinstance(a.get("path"), str):
            paths.append(a["path"])
    for rx in (ATTACHED_RX, UPLOADED_RX):
        for hit in rx.finditer(text):
            paths += [x.strip() for x in hit.group(1).split(",") if x.strip().startswith("/")]
        text = rx.sub("", text)
    paths += MEDIA_RX.findall(text)
    text = MEDIA_RX.sub("", text)
    out, seen = [], set()
    for x in paths:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), out


def file_info(path: str) -> dict:
    """What the page shows for a file. Every attached file gets an id (and is sent), except key/secret files."""
    import hashlib
    import mimetypes

    name = os.path.basename(path)[:200]
    info = {"name": name, "mime": mimetypes.guess_type(name)[0] or "application/octet-stream"}
    try:
        p = Path(path).resolve()
        if not p.is_file() or SECRET_NAME.match(p.name) or any(p.is_relative_to(d) for d in SECRET_DIRS):
            return {**info, "local": True}
        size = p.stat().st_size
        info["size"] = size
        if size > MAX_FILE:
            return {**info, "local": True, "big": True}
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return {**info, "fid": C.b64u(h.digest())[:22], "path": str(p)}
    except (OSError, ValueError):
        return {**info, "local": True}
BACKFILL = 40


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") in (None, "text", "input_text", "output_text"))
    return ""


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


class Sidecar:
    def __init__(self):
        tok = WEBUI / "sidecar-auth" / "cc-agents.token"
        self.token = tok.read_text().strip() if tok.exists() else ""

    def get(self, path: str):
        if not self.token:
            return None
        req = urllib.request.Request(SIDECAR + path, headers={"X-Hermes-Sidecar-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                return json.loads(r.read() or b"{}")
        except (OSError, ValueError):
            return None

    def post(self, path: str, body: dict):
        if not self.token:
            return None
        req = urllib.request.Request(SIDECAR + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Hermes-Sidecar-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read() or b"{}")
        except (OSError, ValueError):
            return None


class Session:
    """What we've already published for one Tabbie session."""

    def __init__(self, sid: str, st: dict):
        self.sid = sid
        self.st = st  # persisted: room, sent (message ids), narr (keys)
        self.mtime = 0.0
        self.offsets: dict[str, int] = {}  # journal file -> bytes read
        self.running = False
        self.stream_id = ""  # current live bubble id
        self.stream_text = ""
        self.last_meta = None
        self.next_draft = 0.0
        self.next_narr = 0.0
        self.queued: set[str] = set()  # message ids waiting to be delivered
        self.sending: set[str] = set()  # file ids on their way


class Mirror:
    def __init__(self, tt, owner: str, config: dict):
        self.tt, self.owner, self.cfg = tt, owner, config
        self.state = tt.load("mirror.json", {"sessions": {}})
        if self.state.get("v", 1) < 2:  # v2 sends files too: send the recent messages again so theirs follow
            for st in self.state["sessions"].values():
                st["sent"] = []
            self.state["v"] = 2
        if self.state["v"] < 3:  # v3: files are only marked sent once the relay stores them; resend any lost before
            for st in self.state["sessions"].values():
                st["files"] = []
                st["sent"] = []  # files go out with their message, so send the recent messages again too
            self.state["v"] = 3
        self.sessions: dict[str, Session] = {}
        self.sidecar = Sidecar()
        self.started = time.time()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.dirty = False

    # ---------- which sessions ----------
    def projects(self) -> list[dict]:
        projects = read_json(WEBUI / "projects.json", []) or []
        if isinstance(projects, dict):
            projects = projects.get("projects", [])
        return [p for p in projects if isinstance(p, dict)]

    def shared_project_ids(self) -> set[str]:
        want = {p.lower() for p in self.cfg.get("projects", [])}
        return {p.get("project_id") for p in self.projects() if str(p.get("name", "")).lower() in want}

    # ---------- chats run here from the team site (runner.py) ----------
    def bind(self, sid: str, room: str):
        """A local chat started for a message sent from the site: it streams into that shared chat's room."""
        st = {"room": room, "sent": [], "narr": [], "bound": True, "remote": {}}
        self.state["sessions"][sid] = st
        self.sessions[sid] = Session(sid, st)
        self.dirty = True

    def expect(self, sid: str, pid: str, text: str):
        """The next user message in sid is this prompt from the site: publish it as the prompt (no duplicate)."""
        st = self.state["sessions"].get(sid)
        if st is not None:
            st.setdefault("remote", {})[pid] = text
            self.dirty = True

    def scan(self):
        pids = self.shared_project_ids()
        extra = set(self.cfg.get("sessions", []))
        rows = read_json(SESS / "_index.json", None)
        if not isinstance(rows, list):
            return  # unreadable right now: change nothing (never take it as "no sessions")
        root = motion_root()
        want = {sid for sid, st in self.state["sessions"].items() if st.get("bound")}  # run here from the site
        for sid in want - set(self.sessions):
            self.sessions[sid] = Session(sid, self.state["sessions"][sid])
        for row in rows:
            sid = row.get("session_id") if isinstance(row, dict) else None
            if not sid or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
                continue
            if sid in self.sessions:
                want.add(sid)
                continue
            if row.get("project_id") in pids or sid in extra or self._project_of(sid) in pids:
                if is_motion(read_json(SESS / f"{sid}.json", {}) or {}, root):
                    continue  # Motion studio chats stay on this Mac
                want.add(sid)
                st = self.state["sessions"].setdefault(sid, {"room": "s-" + secrets.token_hex(8), "sent": [], "narr": []})
                self.sessions[sid] = Session(sid, st)
                self.dirty = True
        # Shared before but not now (project switched off, chat deleted, or a Motion chat): take it off the list.
        for sid in [x for x in self.state["sessions"] if x not in want]:
            st = self.state["sessions"].pop(sid)
            self.sessions.pop(sid, None)
            self.outbox.put_nowait(("index", "session.meta", {"id": st["room"], "removed": True, "updated": int(time.time() * 1000)}, None))
            self.dirty = True

    def _project_of(self, sid: str):
        s = read_json(SESS / f"{sid}.json", {}) or {}
        return s.get("project_id")

    # ---------- publishing ----------
    def emit(self, s: Session, type_: str, payload: dict, done=None):
        self.outbox.put_nowait((s.st["room"], type_, payload, done))

    def meta(self, s: Session, data: dict):
        if s.st.get("bound"):
            return  # the shared chat is someone else's: its title and owner stay theirs
        m = {"id": s.st["room"], "title": (data.get("title") or "Untitled")[:120], "project": self.project_name(data.get("project_id")),
             "owner": self.owner, "updated": int(float(data.get("updated_at") or time.time()) * 1000), "running": s.running}
        fork = self.fork_of(s, data)
        if fork:
            m.update(fork)
        if m != s.last_meta:
            s.last_meta = m
            self.outbox.put_nowait(("index", "session.meta", m, None))

    def fork_of(self, s: Session, data: dict) -> dict | None:
        """A forked chat whose parent is shared too: {"parent": parent's room, "forkAt": id of the last message the
        two share}. Nothing about a parent that isn't shared leaves this Mac."""
        psid = data.get("parent_session_id")
        pst = self.state["sessions"].get(psid) if isinstance(psid, str) else None
        if not pst or psid == s.sid:
            return None
        if s.st.get("fork", {}).get("parent") != pst["room"]:
            parent = read_json(SESS / f"{psid}.json", {}) or {}
            mine, theirs = data.get("messages") or [], parent.get("messages") or []
            at = None
            for i, (a, b) in enumerate(zip(mine, theirs)):
                if a.get("role") != b.get("role") or text_of(a.get("content")) != text_of(b.get("content")):
                    break
                if a.get("role") in ("user", "assistant"):
                    at = str(a.get("message_uid") or a.get("id") or f"i{i}")
            s.st["fork"] = {"parent": pst["room"], "forkAt": at}
            self.dirty = True
        f = s.st["fork"]
        return {"parent": f["parent"], **({"forkAt": f["forkAt"]} if f.get("forkAt") else {})}

    def project_name(self, pid):
        for p in read_json(WEBUI / "projects.json", []) or []:
            if isinstance(p, dict) and p.get("project_id") == pid:
                return p.get("name", "")
        return ""

    # ---------- one pass over a session ----------
    def poll(self, s: Session):
        self.turns(s)
        self.tokens(s)
        p = SESS / f"{s.sid}.json"
        try:
            mt = p.stat().st_mtime
        except OSError:
            return
        data = None
        if mt != s.mtime:
            s.mtime = mt
            data = read_json(p)
        if data:
            s.running = s.running or bool(data.get("active_stream_id"))
            self.messages(s, data)
            self.meta(s, data)
        now = time.time()
        if s.running and now >= s.next_draft:
            s.next_draft = now + 0.3
            self.draft(s)
        if s.running and now >= s.next_narr:
            s.next_narr = now + 1.0
            self.narration(s)

    def messages(self, s: Session, data: dict):
        msgs = data.get("messages") or []
        sent = set(s.st["sent"]) | s.queued
        first_time = not sent
        rows = []
        for i, m in enumerate(msgs):
            if m.get("role") not in ("user", "assistant"):
                continue
            text, paths = split_files(m, text_of(m.get("content")))
            if not text and not paths:
                continue
            mid = str(m.get("message_uid") or m.get("id") or f"i{i}")
            if mid not in sent:
                rows.append((mid, m, text, paths))
            elif paths and m["role"] == "assistant" and i >= len(msgs) - BACKFILL and mid not in s.st.setdefault("refiled", []):
                # sent before every attached file was shared: send it again (same id, so the page replaces it) with its files
                done = set(s.st.get("files") or [])
                if any(f.get("fid") and f["fid"] not in done for f in (file_info(x) for x in paths[:20])):
                    s.st["refiled"].append(mid)
                    del s.st["refiled"][:-300]
                    self.dirty = True
                    rows.append((mid, m, text, paths))
        if first_time:
            rows = rows[-BACKFILL:]
        for mid, m, text, paths in rows:
            if m["role"] == "assistant" and s.running and m is msgs[-1] and not m.get("finish_reason"):
                continue  # still being written; the live draft covers it
            payload = {"id": mid, "role": m["role"], "text": text[:MAX_TEXT], "at": int(float(m.get("timestamp") or time.time()) * 1000)}
            if m["role"] == "user":
                payload["by"] = self.owner
                remote = s.st.get("remote") or {}
                for pid, said in list(remote.items()):
                    if text.strip().endswith(said.strip()):  # sent from the site: show it as that prompt
                        payload["text"], payload["prompt"] = said[:MAX_TEXT], pid
                        del remote[pid]
                        self.dirty = True
                        break
            elif s.stream_id:
                payload["run"] = s.stream_id
                s.stream_id, s.stream_text = "", ""
            files = [file_info(x) for x in paths[:20]]
            if files:
                payload["files"] = [{k: v for k, v in f.items() if k != "path"} for f in files]
            s.queued.add(mid)
            self.emit(s, "message", payload, done=lambda s=s, mid=mid: self.delivered(s, mid))
            for f in files:
                self.send_file(s, f)

    def send_file(self, s: Session, f: dict):
        """A file's bytes, encrypted, in order. Sent once per room; resent from the start after a restart if the
        last chunk never got through."""
        fid = f.get("fid")
        done = s.st.setdefault("files", [])
        if not fid or fid in done or fid in s.sending:
            return
        try:
            data = Path(f["path"]).read_bytes()
        except OSError:
            return
        s.sending.add(fid)
        n = max(1, -(-len(data) // CHUNK))
        for i in range(n):
            part = {"fid": fid, "i": i, "n": n, "name": f["name"], "mime": f["mime"], "size": len(data),
                    "data": C.b64u(data[i * CHUNK:(i + 1) * CHUNK])}
            last = i == n - 1
            self.emit(s, "file", part, done=(lambda s=s, fid=fid: self.file_sent(s, fid)) if last else None)

    def file_sent(self, s: Session, fid: str):
        s.sending.discard(fid)
        s.st.setdefault("files", []).append(fid)
        del s.st["files"][:-300]
        self.dirty = True

    def delivered(self, s: Session, mid: str):
        """Only a message the relay has taken counts as sent (so an outage never loses one)."""
        s.queued.discard(mid)
        s.st["sent"].append(mid)
        del s.st["sent"][:-500]
        self.dirty = True

    def _tail(self, s: Session, path: Path):
        """New complete lines appended to a journal file since last time."""
        try:
            size = path.stat().st_size
        except OSError:
            return []
        off = s.offsets.get(str(path))
        if off is None:  # first sight: only follow files that are new or still growing
            off = 0 if path.stat().st_mtime >= self.started - 2 else size
        if size <= off:
            s.offsets[str(path)] = off
            return []
        with open(path, "rb") as f:
            f.seek(off)
            chunk = f.read(size - off)
        end = chunk.rfind(b"\n") + 1
        s.offsets[str(path)] = off + end
        out = []
        for line in chunk[:end].splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def turns(self, s: Session):
        for path in sorted(SESS.glob(f"_turn_journal/{s.sid}~*.jsonl")):
            for ev in self._tail(s, path):
                kind = ev.get("event")
                if kind == "submitted":
                    s.running = True
                    self.emit(s, "turn.start", {"turn": ev.get("turn_id"), "by": self.owner})
                elif kind == "completed":
                    s.running = False
                    self.emit(s, "turn.end", {"turn": ev.get("turn_id"), "ok": ev.get("terminal") in (None, "completed", "done", True)})
                    s.mtime = 0  # re-read messages: the final reply lands now

    def tokens(self, s: Session):
        d = SESS / "_run_journal" / s.sid
        if not d.is_dir():
            return
        for path in sorted(d.glob("*.jsonl")):
            for ev in self._tail(s, path):
                kind, p = ev.get("event"), ev.get("payload") or {}
                if kind == "token" and isinstance(p.get("text"), str):
                    sid = "run-" + str(ev.get("run_id"))[:40]
                    self.stream(s, sid, (s.stream_text if s.stream_id == sid else "") + p["text"])
                elif kind == "tool" and p.get("name"):
                    self.emit(s, "agent", {"text": f"Using {p['name']}"})

    def stream(self, s: Session, sid: str, full: str):
        full = full[:MAX_TEXT]
        if sid != s.stream_id:
            s.stream_id, s.stream_text = sid, ""
        if full == s.stream_text:
            return
        if full.startswith(s.stream_text):
            self.emit(s, "delta", {"id": sid, "from": len(s.stream_text), "add": full[len(s.stream_text):]})
        else:
            self.emit(s, "delta", {"id": sid, "from": 0, "add": full, "reset": True})
        s.stream_text = full

    def draft(self, s: Session):
        d = self.sidecar.get(f"/draft?id={s.sid}")
        if d and d.get("text"):
            self.stream(s, "draft-" + str(d.get("id"))[:40], d["text"])

    def narration(self, s: Session):
        d = self.sidecar.get(f"/session?id={s.sid}")
        reqs = (d or {}).get("requests") or []
        if not reqs:
            return
        seen = set(s.st["narr"])
        for n in reqs[-1].get("narration") or []:
            key = f"{n.get('at')}|{str(n.get('text'))[:40]}"
            if key in seen or not n.get("text"):
                continue
            self.emit(s, "status", {"text": str(n["text"])[:2000]})
            s.st["narr"].append(key)
            self.dirty = True
        del s.st["narr"][:-300]

    # ---------- loops ----------
    async def watch(self):
        last_scan = 0.0
        while True:
            if time.time() - last_scan > 5:
                if self.tt.config() != self.cfg:
                    return  # shared projects or relay changed: the daemon starts a fresh mirror with the new settings
                self.scan()
                last_scan = time.time()
            for s in list(self.sessions.values()):
                try:
                    self.poll(s)
                except Exception as e:  # noqa: BLE001 (one bad session mustn't stop the rest)
                    print(f"mirror: {s.sid[:6]}: {type(e).__name__}", flush=True)
            if self.dirty:
                self.tt.save("mirror.json", self.state)
                self.dirty = False
            await asyncio.sleep(TICK)

    async def send(self):
        """One WebSocket per room, opened on demand, closed after 10 idle minutes."""
        import websockets

        socks: dict[str, tuple] = {}  # room -> (ws, last_used, prev)
        m, sk, _ = self.tt.mac_keys()
        base = self.tt.RELAY.replace("http", "ws", 1)

        async def sock(room):
            if room in socks and socks[room][0].state.name == "OPEN":
                return socks[room]
            ws = await websockets.connect(f"{base}/api/rooms/{room}/ws?after=999999999",
                                          additional_headers={"authorization": f"Bearer {self.tt.token()}"}, user_agent_header="tabbie-team/1")
            entry = [ws, time.time(), "", {}]  # ws, last used, prev hash, nonce -> event waiting for the relay
            socks[room] = entry

            async def reader():
                waiting = entry[3]
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("body") and msg.get("seq"):
                            entry[2] = C.body_hash(msg["body"])
                            try:
                                item = waiting.pop(json.loads(msg["body"]).get("nonce", ""), None)
                            except ValueError:
                                item = None
                            if item and item[3]:
                                item[3]()  # stored by the relay: now it counts as sent
                        elif msg.get("rejected"):
                            item = waiting.pop(msg.get("nonce", ""), None)
                            if msg["rejected"] == "replay" and item and item[3]:
                                item[3]()  # already stored
                            else:
                                print(f"mirror: relay rejected an event ({msg['rejected']})", flush=True)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    # connection gone before the relay confirmed these: send them again
                    for item in list(waiting.values()):
                        self.outbox.put_nowait(item)
                    waiting.clear()
                    if socks.get(room) is entry:
                        socks.pop(room, None)

            asyncio.create_task(reader())
            return entry

        while True:
            room, type_, payload, done = await self.outbox.get()
            wait = 1.0
            while True:  # in order, never dropped: keep trying until the relay takes it
                entry = e = None
                try:
                    t = self.tt.team()
                    entry = await sock(room)
                    e = C.seal(room, type_, self.owner, m["kid"], sk, t["current"], C.unb64u(t["keys"][t["current"]]), payload, prev=entry[2])
                    if type_ not in ("delta", "typing", "presence"):
                        entry[3][e["nonce"]] = (room, type_, payload, done)  # done() once the relay echoes it back
                    await entry[0].send(json.dumps(e))
                    entry[1] = time.time()
                    break
                except Exception as exc:  # noqa: BLE001 (reconnect and retry)
                    if entry and e:
                        entry[3].pop(e["nonce"], None)  # retried right here, not by the reader
                    socks.pop(room, None)
                    if type_ in ("delta", "typing", "presence"):
                        break  # live-only: stale by the time we're back
                    print(f"mirror: relay unreachable ({type(exc).__name__}), retrying in {wait:.0f} s", flush=True)
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 15)
            for r, (ws, used, _, waiting) in list(socks.items()):
                if time.time() - used > 600 and not waiting:
                    await ws.close()
                    socks.pop(r, None)

    async def run(self):
        print(f"mirror: sharing projects {self.cfg.get('projects', [])} as {self.owner}", flush=True)
        import runner

        sender = asyncio.ensure_future(self.send())
        r = runner.Runner(self)
        listener = asyncio.ensure_future(r.listen())
        models = asyncio.ensure_future(r.publish_models())
        import mailroom

        mail = asyncio.ensure_future(mailroom.MailRoom(self.tt, self.owner).run())
        try:
            await self.watch()
        finally:
            sender.cancel()
            listener.cancel()
            models.cancel()
            mail.cancel()
