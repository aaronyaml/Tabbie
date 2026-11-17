"""The handful of `op` commands Tabbie uses, done with 1Password's own Python SDK instead of the `op` CLI.

Why: `op` reads the 1Password app's private folder on every run. macOS guards that folder ("… would like to
access data from other apps"), so from a background service (the WebUI, the sidecar, a worker) every run can
raise a permission prompt on the Mac, and `op` hangs until someone clicks it. Away from the Mac nobody can, so
secrets looked empty. The SDK talks to 1Password directly with the service account token and never touches the
app, so there is nothing to ask permission for.

Run by secrets.py and bin/with-secrets as
    uv run -q --no-project --with onepassword-sdk==<pin> python -I op_sdk.py <op-style args>
with OP_SERVICE_ACCOUNT_TOKEN in the environment. Same arguments and JSON as `op` for what Tabbie needs:

    vault get VAULT --format json
    item list --vault VAULT --format json                      (id, title, updated_at, tags)
    item get ID --vault VAULT --format json                    (fields; concealed values are NOT included)
    item create --vault VAULT --format json -                  (template JSON on stdin)
    item edit ID --vault VAULT [--tags a,b] [title=…] [name[text]=…]
    item edit ID --vault VAULT --format json -                 (stdin {fields, tags}: replaces all fields)
    item delete ID --vault VAULT [--archive]
    read op://VAULT/ID/FIELD --no-newline                      (the one command that outputs a value)
    run --env-file FILE -- CMD…                                (FILE holds NAME="op://…" references only)

`run` resolves the references, starts CMD with them as environment variables (never the service account
token) and masks any value CMD prints, like `op run` does.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading

from onepassword import types as T
from onepassword.client import Client

MASK = "<concealed by 1Password>"


def die(msg: str, code: int = 1):
    print(f"[ERROR] op sdk {msg}", file=sys.stderr)
    sys.exit(code)


def opt(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else None
    return None


async def client() -> Client:
    tok = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "")
    if not tok:
        die("1Password isn't connected yet.")
    return await Client.authenticate(auth=tok, integration_name="Tabbie", integration_version="1")


async def vault_id(c: Client, name: str) -> str:
    for v in await c.vaults.list():
        if v.id == name or v.title == name:
            return v.id
    die(f"\"{name}\" isn't a vault this service account can use.")


def stamp(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d or "")


def ftype(t: str):
    return T.ItemFieldType.CONCEALED if str(t).upper() == "CONCEALED" else T.ItemFieldType.TEXT


def fields_from(rows: list[dict]) -> list:
    out = []
    for f in rows or []:
        label = str(f.get("label") or f.get("id") or "")
        out.append(T.ItemField(id=str(f.get("id") or label), title=label, field_type=ftype(f.get("type", "")),
                               value=str(f.get("value") or "")))
    return out


async def main(args: list[str]) -> int:
    if args[:1] == ["run"]:
        return await run(args)
    c = await client()
    if args[:2] == ["vault", "get"]:
        vid = await vault_id(c, args[2])
        print(json.dumps({"id": vid, "name": args[2]}))
        return 0
    if args[:1] == ["read"]:
        sys.stdout.write(await c.secrets.resolve(args[1]))
        return 0
    if args[:1] != ["item"] or len(args) < 2:
        die(f"unsupported: {' '.join(args[:2])}", 2)
    vid = await vault_id(c, opt(args, "--vault") or "")
    sub = args[1]
    if sub == "list":
        rows = await c.items.list(vid)
        print(json.dumps([{"id": i.id, "title": i.title, "updated_at": stamp(i.updated_at), "tags": list(i.tags or [])}
                          for i in rows]))
        return 0
    iid = args[2]
    if sub == "get":
        it = await c.items.get(vid, iid)
        print(json.dumps({"id": it.id, "title": it.title, "tags": list(it.tags or []), "fields": [
            {"id": f.id, "label": f.title, "type": "CONCEALED" if f.field_type == T.ItemFieldType.CONCEALED else "STRING",
             "value": "" if f.field_type == T.ItemFieldType.CONCEALED else (f.value or "")} for f in it.fields or []]}))
        return 0
    if sub == "delete":
        await (c.items.archive if "--archive" in args else c.items.delete)(vid, iid)
        return 0
    if sub == "create" and args[-1] == "-":
        t = json.loads(sys.stdin.read() or "{}")
        await c.items.create(T.ItemCreateParams(category=T.ItemCategory.APICREDENTIALS, vault_id=vid, title=t["title"],
                                                fields=fields_from(t.get("fields")), tags=t.get("tags") or []))
        print("{}")
        return 0
    if sub == "edit":
        it = await c.items.get(vid, iid)
        if args[-1] == "-":  # piped: replace every field (like `op item edit … -`)
            patch = json.loads(sys.stdin.read() or "{}")
            it.fields = fields_from(patch.get("fields"))
            if "tags" in patch:
                it.tags = patch["tags"]
        else:
            tags = opt(args, "--tags")
            if tags is not None:
                it.tags = [x for x in tags.split(",") if x]
            for a in args[3:]:
                m = re.fullmatch(r"([A-Za-z0-9_]+)(?:\[text\])?=(.*)", a, re.S)
                if not m or a.startswith("--"):
                    continue
                k, v = m.groups()
                if k == "title":
                    it.title = v
                    continue
                f = next((f for f in it.fields or [] if f.id == k or f.title == k), None)
                if f:
                    f.value = v
                else:
                    it.fields = list(it.fields or []) + [T.ItemField(id=k, title=k, field_type=T.ItemFieldType.TEXT, value=v)]
        await c.items.put(it)
        print("{}")
        return 0
    die(f"unsupported: item {sub}", 2)


def masker(src, dst, values: list[str]):
    for line in iter(src.readline, b""):
        for v in values:
            line = line.replace(v, MASK.encode())
        dst.write(line)
        dst.flush()


async def run(args: list[str]) -> int:
    path = opt(args, "--env-file")
    cmd = args[args.index("--") + 1:] if "--" in args else []
    if not path or not cmd:
        die("usage: run --env-file FILE -- CMD…", 2)
    refs = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r'\s*([A-Z][A-Z0-9_]*)="?(op://[^"\n]+)"?\s*$', line)
        if m:
            refs[m.group(1)] = m.group(2)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OP_")}
    values = []
    if refs:
        c = await client()
        for k, ref in refs.items():
            try:
                env[k] = await c.secrets.resolve(ref)
            except Exception as exc:  # noqa: BLE001 (the SDK's errors name the reference, never a value)
                die(f"couldn't read {k}: {str(exc)[:160]}")
            if len(env[k]) >= 4:
                values.append(env[k].encode())
    values.sort(key=len, reverse=True)
    try:
        p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}", 127)
    env = None
    ts = [threading.Thread(target=masker, args=(p.stdout, sys.stdout.buffer, values), daemon=True),
          threading.Thread(target=masker, args=(p.stderr, sys.stderr.buffer, values), daemon=True)]
    for t in ts:
        t.start()
    rc = p.wait()
    for t in ts:
        t.join()
    return rc


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv[1:])))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 (SDK errors: say what failed, no values involved)
        die(str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__)
