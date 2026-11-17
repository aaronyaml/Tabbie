"""Setup steps added after install are offered once, never when already set up or already seen."""
from __future__ import annotations

from hmem import setup_news as sn


def test_offer_until_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(sn, "SEEN", tmp_path / "setup-seen.json")
    assert [s["id"] for s in sn.pending()] == ["secrets"]
    assert sn.pending({"secrets": True}) == []          # already connected another way: never offered
    sn.mark(["secrets", "not-a-step"])
    assert sn.pending() == [] and sn.seen() == {"secrets"}


def test_every_step_is_optional_or_says_so():
    for s in sn.STEPS:
        assert {"id", "title", "blurb", "added", "optional", "open"} <= set(s)
