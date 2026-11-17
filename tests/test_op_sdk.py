"""op_sdk.py: the SDK stand-in for `op`. No real 1Password: the SDK is stubbed."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "plugin" / "command-centre" / "op_sdk.py"
SECRET = "re_SUPERSECRETVALUE_123"


class FT:  # field types
    CONCEALED, TEXT = "Concealed", "Text"


class Field:
    def __init__(self, id, title, field_type, value="", section_id=None, details=None):
        self.id, self.title, self.field_type, self.value = id, title, field_type, value


class Item:
    def __init__(self, id, title, fields, tags):
        self.id, self.title, self.fields, self.tags = id, title, fields, tags
        self.updated_at = "2026-11-17T00:00:00"


@pytest.fixture
def mod(monkeypatch):
    store = {"i1": Item("i1", "Resend API key", [Field("credential", "credential", FT.CONCEALED, SECRET),
                                                  Field("env", "env", FT.TEXT, "RESEND_API_KEY")], ["tabbie"])}

    class Vaults:
        async def list(self):
            return [types.SimpleNamespace(id="v1", title="Tabbie")]

    class Items:
        async def list(self, vid):
            return list(store.values())

        async def get(self, vid, iid):
            return store[iid]

        async def put(self, it):
            store[it.id] = it

    class Secrets:
        async def resolve(self, ref):
            return SECRET

    class Client:
        @staticmethod
        async def authenticate(**kw):
            return types.SimpleNamespace(vaults=Vaults(), items=Items(), secrets=Secrets())

    op = types.ModuleType("onepassword")
    t = types.ModuleType("onepassword.types")
    t.ItemFieldType, t.ItemField = FT, Field
    c = types.ModuleType("onepassword.client")
    c.Client = Client
    monkeypatch.setitem(sys.modules, "onepassword", op)
    monkeypatch.setitem(sys.modules, "onepassword.types", t)
    monkeypatch.setitem(sys.modules, "onepassword.client", c)
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_test")
    spec = importlib.util.spec_from_file_location("op_sdk_test", HELPER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.store = store
    return m


def test_item_get_never_outputs_concealed_values(mod, capsys):
    asyncio.run(mod.main(["item", "get", "i1", "--vault", "Tabbie", "--format", "json"]))
    out = capsys.readouterr().out
    assert SECRET not in out
    fields = {f["id"]: f for f in json.loads(out)["fields"]}
    assert fields["credential"]["type"] == "CONCEALED" and fields["env"]["value"] == "RESEND_API_KEY"


def test_item_edit_assignments(mod):
    asyncio.run(mod.main(["item", "edit", "i1", "--vault", "Tabbie", "--tags", "tabbie,env:X_KEY",
                          "title=New name", "env[text]=X_KEY", "provider[text]=other"]))
    it = mod.store["i1"]
    assert it.title == "New name" and it.tags == ["tabbie", "env:X_KEY"]
    assert {f.id: f.value for f in it.fields}["env"] == "X_KEY" and {f.id for f in it.fields} >= {"provider"}


def test_masker_hides_values():
    src = io.BytesIO(f"key is {SECRET}\nok\n".encode())
    dst = io.BytesIO()
    # masker has no SDK dependency: load just the function text
    ns = {}
    code = HELPER.read_text()
    exec(code[code.index("def masker"):code.index("async def run")], {"MASK": "<concealed by 1Password>"}, ns)
    ns["masker"](src, dst, [SECRET.encode()])
    assert SECRET.encode() not in dst.getvalue() and b"<concealed by 1Password>" in dst.getvalue()


def test_run_gives_command_secret_not_token(mod, tmp_path, capfd):
    env_file = tmp_path / "e.env"
    env_file.write_text('RESEND_API_KEY="op://Tabbie/i1/credential"\n')
    rc = asyncio.run(mod.run(["run", "--env-file", str(env_file), "--", "sh", "-c",
                              'echo "len=${#RESEND_API_KEY} tok=${OP_SERVICE_ACCOUNT_TOKEN:-none} v=$RESEND_API_KEY"']))
    out = capfd.readouterr().out
    assert rc == 0 and f"len={len(SECRET)}" in out and "tok=none" in out and SECRET not in out
