"""Secrets (1Password): names in, names out; values never leave except a rotated key, once. No real 1Password."""
from __future__ import annotations
import importlib.util, json, sys
from importlib import import_module
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
sec = import_module("command_centre.secrets")
agents = import_module("command_centre.agents")

SECRET = "re_SUPERSECRETVALUE_123"


class FakeOp:
    """Just enough of `op` for the vault: items keep their concealed value; calls are recorded."""
    def __init__(self):
        self.items = {"a" * 26: {"title": "Resend API key", "fields": {"credential": SECRET, "env": "RESEND_API_KEY",
                                                                         "provider": "resend"}, "updated": "1"}}
        self.calls = []

    def __call__(self, args, stdin=None, token=None):
        self.calls.append((list(args), stdin))
        if args[:2] == ["item", "list"]:
            return json.dumps([{"id": i, "title": it["title"], "updated_at": it["updated"]} for i, it in self.items.items()])
        if args[:2] == ["item", "get"]:
            it = self.items[args[2]]
            return json.dumps({"fields": [{"id": k, "label": k, "value": v, "type": "CONCEALED" if k == "credential" else "STRING"}
                                          for k, v in it["fields"].items()]})
        if args[:2] == ["item", "create"]:
            t = json.loads(stdin)
            iid = chr(ord("a") + len(self.items)) * 26
            self.items[iid] = {"title": t["title"], "fields": {f["label"]: f["value"] for f in t["fields"]}, "updated": "1"}
            return "{}"
        if args[:2] == ["item", "edit"]:
            it = self.items[args[2]]
            if stdin:
                for f in json.loads(stdin)["fields"]:
                    it["fields"][f["label"]] = f["value"]
            for a in args[5:]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    if k == "title":
                        it["title"] = v
                    else:
                        it["fields"][k.split("[")[0]] = v
            it["updated"] = str(int(it["updated"]) + 1)
            return "{}"
        if args[:2] == ["item", "delete"]:
            self.items.pop(args[2])
            return ""
        if args[0] == "read":
            iid = args[1].split("/")[3]
            return self.items[iid]["fields"]["credential"]
        raise AssertionError(args)


@pytest.fixture
def op(monkeypatch, tmp_path):
    f = FakeOp()
    monkeypatch.setattr(sec, "_op", f)
    monkeypatch.setattr(sec, "INDEX", tmp_path / "secrets-index.json")
    return f


def test_list_shows_names_never_values(op):
    rows = sec.items()
    assert rows[0]["env"] == "RESEND_API_KEY" and rows[0]["rotatable"] is True
    assert SECRET not in json.dumps(rows)
    assert SECRET not in (sec.INDEX.read_text())


def test_add_sends_value_on_stdin_not_argv(op):
    sec.add({"title": "OpenAI", "env": "openai_api_key", "provider": "openai", "value": "sk-abc"})
    args, stdin = [c for c in op.calls if c[0][:2] == ["item", "create"]][0]
    assert "sk-abc" not in " ".join(args) and "sk-abc" in stdin
    assert any(r["env"] == "OPENAI_API_KEY" for r in sec.items())


def test_add_rejects_duplicate_variable_and_bad_names(op):
    with pytest.raises(sec.SecretError):
        sec.add({"title": "Again", "env": "RESEND_API_KEY", "provider": "resend", "value": "x"})
    with pytest.raises(sec.SecretError):
        sec.add({"title": "Bad", "env": "1BAD", "provider": "other", "value": "x"})


def test_replace_value_goes_on_stdin(op):
    iid = "a" * 26
    sec.update({"id": iid, "value": "re_NEW"})
    edits = [c for c in op.calls if c[0][:2] == ["item", "edit"]]
    assert all("re_NEW" not in " ".join(a) for a, _ in edits)
    assert op.items[iid]["fields"]["credential"] == "re_NEW"


def test_rotate_resend_returns_new_value_once_and_saves_it(op, monkeypatch):
    calls = []

    def http(method, url, token, body=None):
        calls.append((method, url, token))
        if method == "POST":
            return {"id": "key_new", "token": "re_ROTATED"}
        return {}
    monkeypatch.setattr(sec, "_http", http)
    iid = "a" * 26
    res = sec.rotate({"id": iid})
    assert res["value"] == "re_ROTATED" and res["saved"]
    assert calls[0][2] == SECRET  # the old key made the new one
    assert op.items[iid]["fields"]["credential"] == "re_ROTATED" and op.items[iid]["fields"]["key_id"] == "key_new"
    assert "re_ROTATED" not in json.dumps(sec.items())
    # next time the old key's id is known, so it gets deleted with the new key
    calls.clear()
    sec.rotate({"id": iid})
    assert ("DELETE", "https://api.resend.com/api-keys/key_new", "re_ROTATED") in calls


def test_rotate_unsupported_provider_says_so(op):
    sec.add({"title": "Supabase", "env": "SUPABASE_ACCESS_TOKEN", "provider": "supabase", "value": "sbp_x"})
    iid = next(r["id"] for r in sec.items() if r["provider"] == "supabase")
    with pytest.raises(sec.SecretError, match="can't rotate"):
        sec.rotate({"id": iid})


def test_save_token_rejects_non_tokens():
    with pytest.raises(sec.SecretError):
        sec.save_token("not-a-token")
    with pytest.raises(sec.SecretError):
        sec.save_token("ops_" + "a" * 300 + " injected")


def test_env_refs_point_at_items_not_values(op):
    refs = sec.env_refs()
    assert refs == {"RESEND_API_KEY": f"op://{sec.VAULT}/{'a' * 26}/credential"}


def test_worker_rules_name_with_secrets():
    providers = import_module("command_centre.providers")
    rules = providers.worker_rules("claude")
    assert "with-secrets" in rules and "NEVER print" in rules


def test_live_draft_only_whole_words_and_main_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path)
    run = agents.AgentRun("r1", "s1")
    run.base = tmp_path

    def delta(text, parent=None):
        return {"type": "stream_event", "parent_tool_use_id": parent,
                "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}
    run.apply_claude({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text"}}}, now=1)
    run.apply_claude(delta("Hello there, the key is sk-"), now=2)
    d = json.loads((tmp_path / "r1" / "draft.json").read_text())
    assert d["text"] == "Hello there, the key is "  # the half-written word waits
    run.apply_claude(delta("sub-agent words ", parent="toolu_1"), now=3)
    run.draft_write(run._draft, 10, force=True)
    assert "sub-agent" not in json.loads((tmp_path / "r1" / "draft.json").read_text())["text"]
    run.apply_claude({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use"}}}, now=11)
    assert json.loads((tmp_path / "r1" / "draft.json").read_text())["text"] == ""


def test_app_added_item_gets_a_name_and_its_hidden_field(op):
    # added straight in the 1Password app: no Tabbie tags/fields, value in a "password" field
    op.items["z" * 26] = {"title": "OpenAI API key", "fields": {"password": "sk-live"}, "updated": "1"}
    orig = op.__call__

    def call(args, stdin=None, token=None):
        if args[:2] == ["item", "get"] and args[2] == "z" * 26:
            return json.dumps({"fields": [{"id": "password", "label": "password", "value": "sk-live", "type": "CONCEALED"}]})
        return orig(args, stdin, token)
    import command_centre.secrets as s2
    s2._op = call
    row = next(r for r in sec.items() if r["id"] == "z" * 26)
    assert row["env"] == "OPENAI_API_KEY" and row["provider"] == "openai"
    assert sec.env_refs()["OPENAI_API_KEY"] == f"op://{sec.VAULT}/{'z' * 26}/password"
    assert "sk-live" not in json.dumps(sec.items()) and "sk-live" not in sec.INDEX.read_text()


def test_prompts_check_secret_store_before_asking():
    providers = import_module("command_centre.providers")
    questions = import_module("command_centre.questions")
    assert "ALWAYS check the store FIRST" in providers.worker_rules("codex")
    assert "with-secrets --list" in questions.QUESTION_RULES
    assert "with-secrets --list" in providers.PLANNER_ROLE
