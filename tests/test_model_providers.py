"""Model providers beyond the Claude / ChatGPT logins: config, key storage, picker catalog, worker env,
routing and fallbacks. Offline: the key store, model lists and CLIs are faked."""
from __future__ import annotations
import importlib.util, os, sys, tempfile, types
from importlib import import_module
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
mp = import_module("command_centre.model_providers")
ec = import_module("command_centre.executor_choice")
st = import_module("command_centre.state")
pv = import_module("command_centre.providers")


class _Keyring:
    def __init__(self): self.d = {}
    def set_password(self, s, a, k): self.d[(s, a)] = k
    def get_password(self, s, a): return self.d.get((s, a))
    def delete_password(self, s, a): self.d.pop((s, a), None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config + key store, no network, both CLIs installed, both subscriptions enabled."""
    monkeypatch.setattr(mp, "CONFIG", tmp_path / "providers.yaml")
    kr = _Keyring()
    monkeypatch.setattr(mp, "_use_security", lambda: False)
    monkeypatch.setattr(mp, "_keyring", lambda: kr)
    mp._HAS.clear()

    def offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(mp, "_get_json", offline)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    on = ["claude", "codex"]
    monkeypatch.setattr(ec, "_agents", lambda: types.SimpleNamespace(enabled=lambda: list(on)))
    d = Path(tempfile.mkdtemp())
    st.REQUESTS_DIR, st.SESSIONS_DIR = d / "req", d / "ses"
    return types.SimpleNamespace(kr=kr, on=on, tmp=tmp_path)


def _link(pid, key="sk-test-123"):
    mp.set_enabled(pid, True)
    if key:
        mp.set_key(pid, key)


def test_unlinked_providers_are_not_offered(env):
    assert ec.endpoints_on() == []
    cat = ec.public_catalog()
    assert list(cat["providers"]) == ["anthropic", "openai"]
    mp.set_enabled("deepseek", True)  # on, but no key yet: still not offered
    assert "deepseek" not in ec.public_catalog()["providers"]


def test_linked_provider_shows_with_its_billing(env):
    _link("deepseek")
    cat = ec.public_catalog()["providers"]
    assert list(cat) == ["anthropic", "openai", "deepseek"]
    ds = cat["deepseek"]
    assert ds["endpoint"] and ds["billing"] == "pay-per-use" and ds["billing_label"] == "Pay per use"
    assert ds["agent"] == "claude" and ds["worker"] == "Claude Code → DeepSeek"
    assert [m["id"] for m in ds["models"]][:1] == ["deepseek-v4-pro"]
    assert "Pay per use" in ds["models"][0]["desc"]


def test_config_never_holds_the_key(env):
    _link("deepseek", "sk-very-secret")
    assert "sk-very-secret" not in (env.tmp / "providers.yaml").read_text(encoding="utf-8")
    assert mp.get_key("deepseek") == "sk-very-secret"
    pub = mp.public("deepseek", with_models=False)
    assert "sk-very-secret" not in repr(pub) and pub["linked"] is True


def test_bad_keys_and_urls_are_refused(env):
    for bad in ("", "a\nb", "x" * 401):
        with pytest.raises(ValueError):
            mp.set_key("deepseek", bad)
    with pytest.raises(ValueError):
        mp.add_custom("mine", "Mine", "http://evil.example.com", ["m1"])  # plain http only for this Mac
    with pytest.raises(ValueError):
        mp.add_custom("deepseek", "Clash", "https://x.example.com", ["m1"])
    p = mp.add_custom("mine", "Mine", "http://127.0.0.1:8080", ["coder-large"], billing="local", auth="none")
    assert p["worker"] == "claude" and p["models"][0]["tier"] == "top"


def test_choice_on_a_linked_provider(env):
    _link("deepseek")
    ch = ec.set_choice("s1", "deepseek", "deepseek-v4-pro")
    assert (ch["provider"], ch["agent"], ch["endpoint"]) == ("deepseek", "claude", True)
    with pytest.raises(ValueError):
        ec.set_choice("s2", "kimi", "kimi-k2")  # not linked
    mp.delete_key("deepseek")  # unlinked later: the chat falls back to the default
    assert ec.get_choice("s1")["provider"] == "anthropic"


def test_only_a_provider_linked(env):
    """No Claude or ChatGPT plan at all, only DeepSeek: it is the default."""
    env.on.clear()
    env.on.append("nothing")
    _link("deepseek")
    d = ec.default_choice()
    assert d["provider"] == "deepseek" and d["model"] == "deepseek-v4-pro"


def test_claude_env_points_claude_code_at_the_provider(env):
    _link("deepseek", "sk-ds")
    e = mp.claude_env("deepseek", "deepseek-v4-pro")
    assert e["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert e["ANTHROPIC_AUTH_TOKEN"] == "sk-ds" and e["ANTHROPIC_MODEL"] == "deepseek-v4-pro"
    assert e["ANTHROPIC_SMALL_FAST_MODEL"] == "deepseek-flash" and e["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4-pro"
    assert e["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    # Anthropic's own API with a key: just the key, the rest is Claude Code's default
    _link("anthropic-api", "sk-ant-x")
    assert mp.claude_env("anthropic-api", "claude-opus-5-5") == {"ANTHROPIC_API_KEY": "sk-ant-x",
                                                                 "TABBIE_MODEL_PROVIDER": "anthropic-api"}
    mp.set_enabled("kimi", True)
    with pytest.raises(RuntimeError):
        mp.claude_env("kimi", "kimi-k2")  # no key saved


def test_codex_launch(env):
    _link("openai-api", "sk-oa")
    args, e = mp.codex_launch("openai-api")
    assert args == [] and e["CODEX_API_KEY"] == "sk-oa"
    mp.add_custom("resp", "Resp", "https://llm.example.com/v1", ["big-pro"], worker="codex")
    _link("resp", "sk-r")
    args, e = mp.codex_launch("resp")
    assert args[:2] == ["-c", 'model_provider="tabbie_resp"']
    assert 'wire_api="responses"' in args[3] and 'env_key="TABBIE_PROVIDER_KEY"' in args[3]
    assert "sk-r" not in " ".join(args) and e["TABBIE_PROVIDER_KEY"] == "sk-r"
    with pytest.raises(ValueError):
        mp.codex_launch("deepseek")


def test_peer_goes_to_a_linked_provider_at_the_same_tier(env):
    _link("deepseek")
    # Claude <-> Codex stays the first choice
    assert ec.peer_model("anthropic", "claude-opus-5-5")["provider"] == "openai"
    # Codex off: Claude's equivalent is on DeepSeek, same tier
    env.on.remove("codex")
    p = ec.peer_model("anthropic", "claude-haiku-5-5")
    assert (p["provider"], p["model"]) == ("deepseek", "deepseek-flash")
    assert ec.peer_model("anthropic", "claude-opus-5-5")["model"] == "deepseek-v4-pro"
    env.on.append("codex")
    assert ec.peer_model("anthropic", "claude-opus-5-5", exclude=("anthropic", "openai"))["provider"] == "deepseek"
    # from a provider model back to a subscription
    assert ec.peer_model("deepseek", "deepseek-flash")["provider"] == "anthropic"


def test_endpoint_peer_only_returns_linked_providers(env, monkeypatch):
    assert cc._endpoint_peer("anthropic", "claude-opus-5-5") is None
    _link("deepseek")
    alt = cc._endpoint_peer("anthropic", "claude-opus-5-5")
    assert alt["provider"] == "deepseek" and alt["label"] == "DeepSeek V4 Pro"
    assert ec.label("kimi", "K2 Thinking") == "Kimi (Moonshot API) K2 Thinking"
    assert ec.label("kimi", "Kimi K2") == "Kimi K2"


def test_subscription_runs_never_inherit_provider_vars(monkeypatch):
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
              "TABBIE_PROVIDER_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(k, "x")
    a, o = pv._clean_env(pv._ANTHROPIC_BILLING_VARS), pv._clean_env(pv._OPENAI_BILLING_VARS)
    assert not {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL",
                "TABBIE_PROVIDER_KEY"} & set(a)
    assert not {"CODEX_API_KEY", "TABBIE_PROVIDER_KEY"} & set(o)


def test_run_claude_on_an_endpoint_uses_its_env_and_skips_the_login(env, monkeypatch, tmp_path):
    _link("deepseek", "sk-ds")
    seen = {}

    def fake_run(cmd, *, cwd, env, timeout, stdin=None):
        seen["env"], seen["cmd"] = env, cmd
        return 0, '{"type":"result","subtype":"success","result":"hi","session_id":"s1","is_error":false}', ""
    monkeypatch.setattr(pv, "_run", fake_run)
    monkeypatch.setattr(pv, "claude_auth", lambda: (_ for _ in ()).throw(AssertionError("no login check")))

    class NoSlot:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *e): return False
    monkeypatch.setattr(pv, "WorkerSlot", NoSlot)
    res = pv.run_claude("hi", cwd=str(tmp_path), add_dirs=[], cfg={"executor": {}}, resume_session=None,
                        model="deepseek-v4-pro", endpoint="deepseek")
    assert res["ok"], res
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert seen["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-ds"
    assert "sk-ds" not in " ".join(seen["cmd"])
    assert "DeepSeek" in res["billing"]


def test_run_claude_endpoint_without_key_fails_cleanly(env, tmp_path):
    mp.set_enabled("deepseek", True)
    res = pv.run_claude("hi", cwd=str(tmp_path), add_dirs=[], cfg={"executor": {}}, resume_session=None,
                        model="deepseek-v4-pro", endpoint="deepseek")
    assert res["ok"] is False and "no key" in res["error"]


def _sidecar(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("cc_sidecar_providers", PKG / "sidecar.py")
    sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
    smp = sc.model_providers  # the sidecar's own copy of the module
    monkeypatch.setattr(smp, "CONFIG", tmp_path / "sc-providers.yaml")
    kr = _Keyring()
    monkeypatch.setattr(smp, "_use_security", lambda: False)
    monkeypatch.setattr(smp, "_keyring", lambda: kr)
    smp._HAS.clear()
    return sc, smp


def test_sidecar_saves_a_key_without_echoing_it(monkeypatch, tmp_path):
    sc, smp = _sidecar(monkeypatch, tmp_path)
    monkeypatch.setattr(smp, "_get_json", lambda url, key, timeout=12: {"data": [{"id": "deepseek-v4-pro"}]})
    code, out = sc.set_provider({"action": "key", "id": "deepseek", "key": "sk-secret-777"})
    assert code == 200 and out["result"] == {"id": "deepseek", "ok": True, "message": "works"}
    assert "sk-secret-777" not in repr(out)
    ds = next(p for p in out["providers"] if p["id"] == "deepseek")
    assert ds["linked"] and ds["enabled"] and ds["billing_label"] == "Pay per use"
    assert "sk-secret-777" not in repr(sc.providers_payload())


def test_sidecar_reports_a_rejected_key(monkeypatch, tmp_path):
    import urllib.error
    sc, smp = _sidecar(monkeypatch, tmp_path)

    def denied(url, key, timeout=12):
        raise urllib.error.HTTPError(url, 401, "no", {}, None)
    monkeypatch.setattr(smp, "_get_json", denied)
    code, out = sc.set_provider({"action": "key", "id": "kimi", "key": "bad-key"})
    assert code == 200 and out["result"]["ok"] is False and "rejected" in out["result"]["message"]
    assert smp.get_key("kimi") is None and "kimi" not in smp.enabled_ids()  # a wrong key isn't kept


def test_sidecar_custom_endpoint_and_remove(monkeypatch, tmp_path):
    sc, smp = _sidecar(monkeypatch, tmp_path)
    monkeypatch.setattr(smp, "_get_json", lambda *a, **k: {"models": [{"name": "qwen3-coder:30b"}]})
    code, out = sc.set_provider({"action": "add", "id": "lab", "name": "Lab box", "base_url": "http://127.0.0.1:9000",
                                 "models": "qwen3-coder:30b", "auth": "none", "billing": "local"})
    assert code == 200 and any(p["id"] == "lab" and p["custom"] for p in out["providers"])
    assert sc.set_provider({"action": "add", "id": "x", "base_url": "ftp://nope", "models": []})[0] == 400
    assert sc.set_provider({"action": "remove", "id": "lab"})[0] == 200
    assert not smp.get("lab")
    assert sc.set_provider({"action": "key", "id": "nope", "key": "k"})[0] == 404


def test_effort_only_where_the_api_takes_it(env):
    _link("deepseek"); _link("anthropic-api", "sk-a")
    cat = ec.public_catalog()["providers"]
    assert cat["anthropic"]["effort"] and cat["anthropic-api"]["effort"] and not cat["deepseek"]["effort"]


def test_flock_shim_excludes_a_second_holder(tmp_path):
    from hmem import flockcompat as fcntl
    p = tmp_path / "x.lock"
    a, b = open(p, "w", encoding="utf-8"), open(p, "w", encoding="utf-8")
    fcntl.flock(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(BlockingIOError):
        fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(a, fcntl.LOCK_UN)
    fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(b, fcntl.LOCK_UN)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_windows_credential_manager_round_trip():
    from hmem import wincred
    svc, acct = "tabbie-test", f"ci-{os.getpid()}"
    wincred.set_password(svc, acct, "sk-test-ünïcode")
    try:
        assert wincred.get_password(svc, acct) == "sk-test-ünïcode"
    finally:
        wincred.delete_password(svc, acct)
    assert wincred.get_password(svc, acct) is None
    wincred.delete_password(svc, acct)  # already gone: no error
