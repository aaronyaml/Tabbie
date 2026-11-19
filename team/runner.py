"""Run messages sent from the team site on this Mac (Phase 3).

Someone types into a shared chat on the team site. Their browser (one of their own approved devices) puts two
signed, encrypted events on the relay: the "prompt" itself in the chat's room (so everyone sees it), and a small
"queue" pointer {kind: "run", room, prompt, to} in the "index" room. Only the sender's own Mac acts on it:

  1. the pointer must come from this Mac's owner and be addressed to them;
  2. the prompt it points at must pass ttcore.Verifier with can_execute: signed by one of the owner's approved
     devices (never this Mac, never anyone else), fresh (2 minutes), and never run before (seen.json);
  3. it runs through this Mac's own Tabbie WebUI, exactly like a typed message (POST /api/chat/start).

Where it runs: if the room is one of this Mac's own shared chats, in that chat. Otherwise in a new local chat that
starts with the shared chat's conversation so far, "bound" to the same room, so the mirror streams the reply,
tools, files and progress back into the chat the person typed in.

The WebUI login: if the WebUI has a password, it's read at runtime from the WebUI's own .env (the same file its
launcher loads), kept in memory only, never logged.
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import ttcore as C

ROOT = Path(__file__).resolve().parents[1]
WEBUI_URL = os.environ.get("TT_WEBUI_URL") or f"http://127.0.0.1:{os.environ.get('HERMES_WEBUI_PORT') or 8787}"
WEBUI_ENV = Path(os.environ.get("TT_WEBUI_ENV") or ROOT / "hermes-webui" / ".env")
PROMPT_RX = re.compile(r"^p-[A-Za-z0-9_-]{8,40}$")
ROOM_RX = re.compile(r"^s-[a-z0-9-]{4,60}$")
FID_RX = re.compile(r"^[A-Za-z0-9_-]{22}$")
MAX_PROMPT = 100_000
CONTEXT_CHARS = 30_000
MAX_FILE = 10 * 1024 * 1024


class WebUIError(Exception):
    pass


def _password() -> str:
    pw = os.environ.get("HERMES_WEBUI_PASSWORD", "")
    if pw:
        return pw
    try:
        for line in WEBUI_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            k, sep, v = line.partition("=")
            if sep and k.strip() == "HERMES_WEBUI_PASSWORD":
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                return v
    except OSError:
        pass
    return ""


class WebUI:
    """This Mac's Tabbie WebUI, over its own HTTP API (as a non-browser client: no Origin/Referer)."""

    def __init__(self, base: str = WEBUI_URL):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _req(self, path: str, body: bytes | None, ctype: str, timeout: float) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, data=body, method="POST" if body is not None else "GET",
                                     headers={"content-type": ctype, "user-agent": "tabbie-team/1"})
        try:
            with self.http.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise WebUIError("Tabbie isn't answering on this Mac.") from e

    def call(self, path: str, body: bytes | None = None, ctype: str = "application/json", timeout: float = 60) -> tuple[int, dict]:
        code, out = self._req(path, body, ctype, timeout)
        if code == 401:
            self.login()
            code, out = self._req(path, body, ctype, timeout)
        return code, out

    def login(self):
        pw = _password()
        if not pw:
            raise WebUIError("Tabbie on this Mac needs a sign-in the team service can't do.")
        code, _ = self._req("/api/auth/login", json.dumps({"password": pw}).encode(), "application/json", 20)
        if code != 200:
            raise WebUIError("The team service couldn't sign in to Tabbie on this Mac.")

    def post(self, path: str, body: dict, timeout: float = 60) -> tuple[int, dict]:
        return self.call(path, json.dumps(body).encode(), timeout=timeout)

    def new_session(self, project_id: str | None) -> str:
        body = {"worktree": False}
        if project_id:
            body["project_id"] = project_id
        code, out = self.post("/api/session/new", body)
        sid = (out.get("session") or {}).get("session_id") if code == 200 else None
        if not sid:
            raise WebUIError("Tabbie couldn't start a new chat.")
        return sid

    def rename(self, sid: str, title: str):
        self.post("/api/session/rename", {"session_id": sid, "title": title[:120]})

    def upload(self, sid: str, name: str, mime: str, data: bytes) -> dict | None:
        boundary = uuid.uuid4().hex
        safe = re.sub(r'[\r\n"\\/]', "_", name)[:200] or "file"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{sid}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\n"
            f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n".encode(),
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        code, out = self.call("/api/upload", b"".join(parts), f"multipart/form-data; boundary={boundary}", 120)
        if code != 200 or not out.get("path"):
            return None
        return {"name": out.get("filename") or safe, "path": out["path"], "mime": out.get("mime") or mime,
                "size": out.get("size", len(data)), "is_image": bool(out.get("is_image"))}

    def start(self, sid: str, message: str, attachments: list[dict]) -> tuple[int, dict]:
        body = {"session_id": sid, "message": message}
        if attachments:
            body["attachments"] = attachments
        return self.post("/api/chat/start", body, timeout=90)


def context_of(history: list[dict], title: str, owner: str, prompt: str) -> str:
    """The shared chat so far, for a new chat on this Mac, then the new message."""
    lines, used = [], 0
    for h in reversed(history):
        who = "Tabbie" if h["role"] == "assistant" else h["by"].capitalize()
        text = h["text"].strip()
        if len(text) > 4000:
            text = text[:4000] + " …"
        block = f"**{who}:** {text}"
        if used + len(block) > CONTEXT_CHARS:
            break
        lines.append(block)
        used += len(block)
    lines.reverse()
    head = f"This message comes from the shared team chat “{title}”, started by {owner.capitalize()} on their Mac."
    if lines:
        head += " The conversation so far:\n\n" + "\n\n".join(lines)
    return f"{head}\n\n---\n\n{prompt}"


class Runner:
    def __init__(self, mirror):
        self.m = mirror
        self.tt = mirror.tt
        self.owner = mirror.owner
        self.webui = WebUI()
        self.v = None
        self.metas: dict[str, dict] = {}  # room -> latest session.meta (title, project, owner)
        self.busy: set[str] = set()

    # ---------- relay ----------
    def verifier(self) -> C.Verifier:
        if self.v is None:
            self.v = self.tt.verifier()
        return self.v

    def open(self, e: dict, room: str, seq: int) -> dict | None:
        try:
            return self.verifier().open(e, room, seq)
        except C.Rejected as r:
            if "unknown" in str(r):  # a device approved since we loaded the directory: reload once
                self.v = None
                try:
                    return self.verifier().open(e, room, seq)
                except C.Rejected:
                    return None
            return None

    async def _socket(self, room: str, after: int):
        import websockets

        url = self.tt.RELAY.replace("http", "ws", 1) + f"/api/rooms/{room}/ws?after={after}"
        return await websockets.connect(url, additional_headers={"authorization": f"Bearer {self.tt.token()}"},
                                        user_agent_header="tabbie-team/1", max_size=2**20)

    async def history(self, room: str) -> list[tuple[dict, dict]]:
        """Every stored event in a room, verified: [(relay msg, opened)]."""
        out, after = [], 0
        while True:
            ws = await self._socket(room, after)
            more = False
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
                    if "caught_up" in msg:
                        more = not msg["caught_up"]
                        break
                    if not msg.get("seq") or not msg.get("body"):
                        continue
                    after = max(after, msg["seq"])
                    try:
                        e = json.loads(msg["body"])
                    except ValueError:
                        continue
                    o = self.open(e, room, msg["seq"])
                    if o:
                        out.append((msg, o))
            finally:
                await ws.close()
            if not more:
                return out

    def say(self, room: str, pid: str, text: str, ok: bool = True):
        self.m.outbox.put_nowait((room, "status", {"for": pid, "text": text, "ok": ok}, None))

    # ---------- the models this Mac can run, for the site's picker ----------
    def models(self) -> dict | None:
        """The picker catalog from this Mac's Tabbie, trimmed to what the site shows: {providers: [{id, name,
        models: [{id, name}]}], default: {provider, model}}. None if Tabbie's helper isn't answering."""
        d = self.m.sidecar.get("/executor")
        cat = (d or {}).get("catalog") or {}
        provs = []
        for pid, pr in (cat.get("providers") or {}).items():
            ms = [{"id": str(m.get("id")), "name": str(m.get("name") or m.get("id"))[:60]}
                  for m in (pr.get("models") or []) if isinstance(m, dict) and m.get("id")][:20]
            if ms:
                provs.append({"id": str(pid)[:40], "name": str(pr.get("name") or pid)[:40], "models": ms})
        if not provs:
            return None
        ch = (d or {}).get("choice") or {}
        return {"kind": "models", "providers": provs[:8],
                "default": {"provider": str(ch.get("provider") or ""), "model": str(ch.get("model") or "")}}

    async def publish_models(self):
        """Tell the site (once, and again whenever it changes) which models this Mac can run."""
        while True:
            try:
                cat = await asyncio.to_thread(self.models)
                if cat:
                    key = json.dumps(cat, sort_keys=True)
                    if (self.tt.load("models.json") or {}).get("key") != key:
                        self.m.outbox.put_nowait(("index", "status", cat, None))
                        self.tt.save("models.json", {"key": key})
            except Exception as exc:  # noqa: BLE001
                print(f"runner: models: {type(exc).__name__}", flush=True)
            await asyncio.sleep(15 * 60)

    def use_model(self, sid: str, want) -> str | None:
        """Switch this chat to the model picked on the site. Returns the model's name, or None if it isn't one
        this Mac offers (then the chat keeps its own)."""
        if not isinstance(want, dict):
            return None
        prov, model = str(want.get("provider") or ""), str(want.get("model") or "")
        cat = self.models() or {}
        for pr in cat.get("providers", []):
            if pr["id"] == prov:
                for m in pr["models"]:
                    if m["id"] == model:
                        r = self.m.sidecar.post("/executor", {"id": sid, "provider": prov, "model": model, "effort": ""})
                        return m["name"] if r and not r.get("error") else None
        return None

    # ---------- the index room: pointers to prompts ----------
    async def listen(self):
        # From the start each time: the chat list (titles, owners, projects) comes from it. Old run pointers are
        # harmless: a prompt only runs if it's fresh (2 minutes) and never ran before (seen.json).
        after = 0
        wait = 1.0
        while True:
            try:
                ws = await self._socket("index", after)
                wait = 1.0
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "caught_up" in msg:
                            if not msg["caught_up"]:
                                break  # reconnect from `after` for the next page
                            continue
                        seq = msg.get("seq")
                        if not seq or not msg.get("body"):
                            continue
                        after = max(after, seq)
                        self.on_index(msg, seq)
                finally:
                    await ws.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 (reconnect)
                print(f"runner: relay unreachable ({type(exc).__name__}), retrying in {wait:.0f} s", flush=True)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 30)

    def on_index(self, msg: dict, seq: int):
        if msg.get("type") not in ("session.meta", "queue", "status"):
            return
        try:
            e = json.loads(msg["body"])
        except ValueError:
            return
        o = self.open(e, "index", seq)
        if not o:
            return
        p = o["payload"] if isinstance(o["payload"], dict) else {}
        if msg["type"] == "status":  # a teammate's Mac publishing a project (teamprojects.py)
            if getattr(self.m, "follower", None) and p.get("kind") == "project":
                self.m.follower.offer(e, p)
            return
        if msg["type"] == "session.meta":
            if ROOM_RX.match(str(p.get("id", ""))):
                self.metas[p["id"]] = {"title": str(p.get("title") or "Shared chat")[:120], "project": str(p.get("project") or ""),
                                       "owner": e["author"]}
            return
        if (e["author"] != self.owner or p.get("kind") != "run" or p.get("to") != self.owner
                or not ROOM_RX.match(str(p.get("room", ""))) or not PROMPT_RX.match(str(p.get("prompt", "")))):
            return
        if abs(C.now_ms() - int(e["ts"])) > C.EXEC_WINDOW_MS or p["prompt"] in self.busy:
            return
        self.busy.add(p["prompt"])
        asyncio.ensure_future(self.run(p["room"], p["prompt"]))

    # ---------- running one prompt ----------
    async def run(self, room: str, pid: str):
        try:
            await self._run(room, pid)
        except WebUIError as e:
            self.say(room, pid, str(e), ok=False)
        except Exception as e:  # noqa: BLE001
            print(f"runner: {pid[:8]}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            self.say(room, pid, "Something went wrong on the Mac running this. Try sending it again.", ok=False)
        finally:
            self.tt.remember_seen(self.verifier())

    async def _run(self, room: str, pid: str):
        before = set(self.verifier().seen)
        events = await self.history(room)
        # Reading the room opens other prompts too; leave those unread so each still runs on its own pointer.
        for msg, _o in events:
            if msg.get("type") == "prompt":
                n = json.loads(msg["body"]).get("nonce")
                if n not in before and (_o["payload"] or {}).get("id") != pid:
                    self.verifier().seen.pop(n, None)
        prompt, history, chunks = None, [], {}
        for msg, o in events:
            p = o["payload"] if isinstance(o["payload"], dict) else {}
            t = msg.get("type")
            if t == "prompt" and p.get("id") == pid:
                prompt = (msg, o, p)
            elif t == "message" and p.get("role") in ("user", "assistant") and isinstance(p.get("text"), str):
                history.append({"role": p["role"], "by": str(p.get("by") or msg["author"]), "text": p["text"]})
            elif t == "prompt" and isinstance(p.get("text"), str):
                history.append({"role": "user", "by": msg["author"], "text": p["text"]})
            elif t == "file" and msg["author"] == self.owner and FID_RX.match(str(p.get("fid", ""))):
                chunks.setdefault(p["fid"], {})[int(p.get("i", 0))] = p
        if not prompt:
            return  # not (yet) there or not genuine: nothing to do
        msg, o, p = prompt
        if not o["can_execute"]:
            return  # not from this owner's own device, too old, or already run
        text = str(p.get("text") or "")[:MAX_PROMPT]
        wanted = [f for f in (p.get("files") or [])[:10] if isinstance(f, dict) and FID_RX.match(str(f.get("fid", "")))]
        mac = (self.tt.load("mac.json") or {}).get("name") or "your Mac"
        self.say(room, pid, f"{mac} got it")

        # where to run it
        sid = next((s for s, st in self.m.state["sessions"].items() if st.get("room") == room), None)
        fresh = sid is None
        meta = self.metas.get(room, {})
        if fresh:
            pid_local = None
            if meta.get("project"):
                if getattr(self.m, "follower", None):  # a teammate's project: start from their latest code
                    await asyncio.to_thread(self.m.follower.freshen_project, meta["project"])
                for pr in self.m.projects():
                    if str(pr.get("name", "")).lower() == meta["project"].lower():
                        pid_local = pr.get("project_id")
            sid = await asyncio.to_thread(self.webui.new_session, pid_local)
            await asyncio.to_thread(self.webui.rename, sid, meta.get("title") or "Shared chat")
            self.m.bind(sid, room)
        message = context_of(history, meta.get("title") or "Shared chat", meta.get("owner") or "a teammate", text) if fresh else text
        if not message.strip():
            message = "(see the attached files)"
        if p.get("model"):
            name = await asyncio.to_thread(self.use_model, sid, p.get("model"))
            if name:
                self.say(room, pid, f"Using {name}")
        self.m.expect(sid, pid, text)

        # files sent with it
        atts = []
        for f in wanted:
            parts = chunks.get(f["fid"], {})
            n = int(next(iter(parts.values()), {}).get("n", 0) or 0)
            if not n or len(parts) != n:
                continue
            try:
                data = b"".join(C.unb64u(parts[i]["data"]) for i in range(n))
            except (KeyError, ValueError):
                continue
            if len(data) > MAX_FILE:
                continue
            a = await asyncio.to_thread(self.webui.upload, sid, str(f.get("name") or "file"), str(f.get("mime") or ""), data)
            if a:
                atts.append(a)

        # start it; if a reply is already being written, wait for it to finish
        told = False
        deadline = time.time() + 30 * 60
        while True:
            code, out = await asyncio.to_thread(self.webui.start, sid, message, atts)
            if code == 200:
                return
            if code == 409 and out.get("code") == "session_rotated" and out.get("continuation_session_id"):
                sid = out["continuation_session_id"]
                continue
            if code == 409 and "active stream" in str(out.get("error", "")) and time.time() < deadline:
                if not told:
                    self.say(room, pid, "Waiting for the current reply to finish")
                    told = True
                await asyncio.sleep(3)
                continue
            raise WebUIError(f"Tabbie on {mac} couldn't start this ({code}).")
