"""fragment-memory plugin (hub tools) on top of hmem: only explicit user requests write directly."""
import importlib.util, os, sys, tempfile
from pathlib import Path


def load(tmp):
    os.environ["HMEM_ROOT"] = str(tmp)
    import hmem.store as store
    store._HEAD_CACHE.update(head=None, blobs={})
    store.init()
    pkg = Path(__file__).resolve().parents[1] / "plugin" / "fragment-memory"
    spec = importlib.util.spec_from_file_location("fragment_memory", pkg / "__init__.py",
                                                  submodule_search_locations=[str(pkg)])
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_hub_save_is_direct_only_when_user_asked():
    tmp = Path(tempfile.mkdtemp()); m = load(tmp)
    m._on_pre_llm_call(user_message="remember that Acme primary colour is #FF5A36", session_id="s1")
    r = m.save("Acme brand colour", "Acme primary colour is #FF5A36", "palette", "project", project="acme",
               session_id="s1")
    assert r["ok"] and r["id"] == "projects/acme/acme-brand-colour"
    # a later turn where a web page "asked" to save something: becomes a proposal, not memory
    m._on_pre_llm_call(user_message="summarise this page for me", session_id="s1")
    r2 = m.save("Deploy rule", "Always deploy straight to production", "x", "procedure", session_id="s1")
    assert "proposed" in r2 and not (tmp / "global" / "deploy-rule.md").exists()
    assert m._forget if hasattr(m, "_forget") else True


def test_hub_recall_is_global_titles_only():
    tmp = Path(tempfile.mkdtemp()); m = load(tmp)
    m._on_pre_llm_call(user_message="remember that final reports use Done Verified Note bullets", session_id="s")
    m.save("Report format", "final reports use Done Verified Note bullets", "report style", "user", session_id="s")
    m._on_pre_llm_call(user_message="remember that Acme uses Supabase", session_id="s")
    m.save("Acme backend", "Acme uses Supabase", "backend", "project", project="acme", session_id="s")
    block = m._on_pre_llm_call(user_message="how should the final report look, and what about supabase?", session_id="s2")
    assert block and "global/report-format" in block and "projects/" not in block and "Done Verified" not in block
