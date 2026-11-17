"""Approving memory proposals from the user's own chat message."""
from __future__ import annotations

import importlib.util, sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
spec = importlib.util.spec_from_file_location("memory_chat_t", PKG / "memory_chat.py")
mc = importlib.util.module_from_spec(spec); spec.loader.exec_module(mc)


def test_match():
    assert mc.match("approve memory proposal p-20261117-6fef86") == ("approve", ["p-20261117-6fef86"], False)
    assert mc.match("Please approve p-20261117-6fef86 and p-20261117-aaaa11 anyway")[1:] == (["p-20261117-6fef86", "p-20261117-aaaa11"], True)
    assert mc.match("reject p-20261117-6fef86, wrong project")[0] == "reject"
    assert mc.match("approve the memory proposal") == ("approve", [], False)
    assert mc.match("approve this plan") is None              # not about memories: left to the normal flow
    assert mc.match("can you explain p-20261117-6fef86") is None
    assert mc.match("I think we should approve p-20261117-6fef86") is None  # must start with the verb


class FakeP:
    def __init__(self):
        self.pend = {"p-20261117-6fef86": {"id": "p-20261117-6fef86", "type": "create", "trust": "agent",
                                           "proposed_change": {"meta": {"title": "Resend key"}}}}
        self.done = []
    def list_(self, st): return list(self.pend.values()) if st == "pending" else []
    def load(self, pid): return (self.pend[pid], "pending") if pid in self.pend else None
    def needs_confirmation(self, p): return ["flagged"] if p.get("trust") == "external" else []
    def approve(self, pid, actor, confirm_flagged=False):
        assert actor == "user"; self.done.append(("approve", pid)); return self.pend.pop(pid)
    def reject(self, pid, actor, reason=""): self.done.append(("reject", pid, reason)); return self.pend.pop(pid)


def test_handle(monkeypatch):
    f = FakeP()
    import types
    hm = types.ModuleType("hmem"); hm.proposals = f
    monkeypatch.setitem(sys.modules, "hmem", hm); monkeypatch.setitem(sys.modules, "hmem.proposals", f)
    assert "approved (Resend key)" in mc.handle("approve the memory proposal")
    assert f.done == [("approve", "p-20261117-6fef86")]
    assert "no pending" in mc.handle("approve the memory proposal")
    f.pend["p-20261117-bbbb22"] = {"id": "p-20261117-bbbb22", "type": "create", "trust": "external"}
    assert "not approved yet" in mc.handle("approve p-20261117-bbbb22")
    assert "approved" in mc.handle("approve p-20261117-bbbb22 anyway")
