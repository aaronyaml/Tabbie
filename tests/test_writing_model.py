"""Cheaper model for writing (Agents & behaviour): writing-only requests run on the mid/fast model, mixed jobs
get a rule to hand their writing to a cheaper subagent, and the switch turns both off."""
from __future__ import annotations
import importlib.util, sys
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
cc = sys.modules["command_centre"]
router = import_module("command_centre.router")
providers = import_module("command_centre.providers")
from hmem import agents as agent_set  # noqa: E402

DOCS = ["draft an email to the landlord about the boiler", "write a README for this repo",
        "write a proposal for the new pricing", "write a blog post about our launch", "draft release notes for v2",
        "proofread my cover letter"]
NOTES = ["write up meeting notes from this transcript", "summarise this PDF", "jot down notes from our call"]
NOT_WRITING = ["fix the README typo", "write tests for the login", "build a docs page for the app",
               "write a function that parses dates", "edit the hero image", "redesign the settings page",
               "write a script in deploy.py that pushes the build", "what's the weather"]


def test_writing_task_kinds():
    for t in DOCS:
        assert router.writing_task(t) == "doc", t
    for t in NOTES:
        assert router.writing_task(t) == "note", t
    for t in NOT_WRITING:
        assert router.writing_task(t) is None, t


class Req(dict):
    def __init__(self, text):
        super().__init__(original_prompt=text)
        self.id = "r-test"
        self.data = {"raw_message": text}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _real_agents(monkeypatch):
    monkeypatch.setattr(cc, "agents", lambda: agent_set)  # older tests swap cc.agents for a stub and leave it


def _choice(provider="anthropic", model="claude-opus-5-5", default=True):
    return {"provider": provider, "model": model, "name": model, "effort": None, "default": default}


def test_writing_request_runs_on_the_cheaper_model(monkeypatch):
    monkeypatch.setattr(cc, "_codex_models_safe", lambda: ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"])
    assert cc._writing_choice(Req(DOCS[0]), _choice())["model"] == "claude-sonnet-5-5"
    assert cc._writing_choice(Req(NOTES[0]), _choice())["model"] == "claude-haiku-5-5"
    assert cc._writing_choice(Req(DOCS[1]), _choice("openai", "gpt-6.1-sol"))["model"] == "gpt-6.1-terra"
    assert cc._writing_choice(Req(NOTES[1]), _choice("openai", "gpt-6-astra"))["model"] == "gpt-6-luna"


def test_writing_model_leaves_other_cases_alone(monkeypatch):
    monkeypatch.setattr(cc, "_codex_models_safe", lambda: ["gpt-6.1-sol"])
    # a model picked for this chat wins
    assert cc._writing_choice(Req(DOCS[0]), _choice(default=False))["model"] == "claude-opus-5-5"
    # never a bigger model than the chat's
    assert cc._writing_choice(Req(DOCS[0]), _choice(model="claude-haiku-5-5"))["model"] == "claude-haiku-5-5"
    # not writing
    assert cc._writing_choice(Req(NOT_WRITING[0]), _choice())["model"] == "claude-opus-5-5"
    # Codex login doesn't offer Terra: stays put
    assert cc._writing_choice(Req(DOCS[0]), _choice("openai", "gpt-6.1-sol"))["model"] == "gpt-6.1-sol"
    # a linked model provider is never swapped
    assert cc._writing_choice(Req(DOCS[0]), _choice("deepseek", "deepseek-chat"))["model"] == "deepseek-chat"


def test_switch_off_turns_both_parts_off():
    assert agent_set.writing_model_enabled()  # default on
    assert "CHEAPER MODEL FOR WRITING" in providers.worker_rules("claude")
    assert 'model "sonnet"' in providers.worker_rules("claude")
    assert "gpt-6.1-terra" in providers.worker_rules("codex")
    agent_set.save(writing_model=False)
    assert not agent_set.writing_model_enabled()
    assert "CHEAPER MODEL FOR WRITING" not in providers.worker_rules("claude")
    assert cc._writing_choice(Req(DOCS[0]), _choice())["model"] == "claude-opus-5-5"
    agent_set.save(writing_model=True)  # the other switches keep their values
    assert agent_set.writing_model_enabled() and agent_set.uat_enabled()


def test_not_now_says_where_to_find_secrets():
    from hmem import setup_news
    s = next(x for x in setup_news.STEPS if x["id"] == "secrets")
    assert "Settings → Accounts → Secrets" in s["later"]
