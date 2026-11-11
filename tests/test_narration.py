"""Live narration (worker text between tool calls) and images attached mid-run (ATTACH: lines)."""
from __future__ import annotations
import importlib.util, json, os, struct, sys, zlib
from importlib import import_module
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "plugin" / "command-centre"
if "command_centre" not in sys.modules:
    spec = importlib.util.spec_from_file_location("command_centre", PKG / "__init__.py",
                                                  submodule_search_locations=[str(PKG)])
    _m = importlib.util.module_from_spec(spec); sys.modules["command_centre"] = _m
    spec.loader.exec_module(_m)
agents = import_module("command_centre.agents")
providers = import_module("command_centre.providers")
cc = sys.modules["command_centre"]

RID = "20261102-120000-abc123"


def _png(path: Path, w=4, h=3) -> Path:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def _text(t, mid):
    return {"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": t}]}}


def _tool(name, mid, tid, **inp):
    return {"type": "assistant", "message": {"id": mid, "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def texts(run):
    return [n["text"] for n in run.data.get("narration") or [] if n.get("kind", "text") == "text"]


def ops(run):
    return [(n["tool"], n["text"], n["done"], n["status"]) for n in run.data.get("narration") or [] if n.get("kind") == "op"]


def _run(tmp_path, monkeypatch, roots=None):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "requests")
    run = agents.AgentRun(RID, "sess-1")
    run.attach_roots = [str(r) for r in (roots or [])]
    run.start_main("Claude Code", "claude", now=1000.0)
    return run


# ---- narration -------------------------------------------------------------------------------

def test_clean_narration_drops_code_attach_and_secrets():
    t = "Let me check the CSS first.\n```bash\nrm -rf x\n```\nATTACH: /tmp/a.png\nkey sk-ant-abcdefghijklmnopqrstuv"
    line = agents.clean_narration(t)
    assert line.startswith("Let me check the CSS first.")
    assert "rm -rf" not in line and "ATTACH" not in line and "sk-ant-" not in line and "[REDACTED]" in line
    assert agents.clean_narration("```\nonly code\n```") is None
    assert len(agents.clean_narration("word " * 200)) == agents.NARRATION_CHARS


def test_claude_narration_between_tools_final_answer_dropped(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    evs = [_text("Let me check the CSS first.", "m1"), _tool("Read", "m1", "t1", file_path="/x/a.css"),
           _text("Found it, now fixing.", "m2"), _tool("Edit", "m2", "t2", file_path="/x/a.css"),
           _text("Done: fixed the CSS.", "m3")]
    for i, ev in enumerate(evs):
        run.apply_claude(ev, now=1001.0 + i)
    # live: the latest text shows straight away (it may be narration or the answer; not known yet)
    assert texts(run) == ["Let me check the CSS first.", "Found it, now fixing.", "Done: fixed the CSS."]
    kinds = [n["kind"] for n in run.data["narration"]]
    assert kinds == ["text", "op", "text", "op", "text"]  # interleaved, in order
    assert ops(run) == [("Read", "Reading a.css", "Read a.css", "running"), ("Edit", "Editing a.css", "Edited a.css", "running")]
    run.apply_claude({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}})
    run.apply_claude({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "x"}]}})
    assert [o[3] for o in ops(run)] == ["done", "failed"]
    run.apply_claude({"type": "result", "subtype": "success", "result": "Done: fixed the CSS.", "duration_ms": 9000})
    assert texts(run) == ["Let me check the CSS first.", "Found it, now fixing."]
    assert run.data["narration"][-1]["kind"] == "op"


def test_narration_only_main_worker_and_deduped(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.apply_claude(_tool("Agent", "m1", "sub1", description="Look around"), now=1001)
    run.apply_claude({**_text("subagent words", "s1"), "parent_tool_use_id": "sub1"}, now=1002)
    run.apply_claude(_text("Checking the build.", "m2"), now=1003)
    run.apply_claude(_text("Checking the build.", "m2"), now=1004)  # same block re-sent
    assert texts(run) == ["Checking the build."]
    assert [o[0] for o in ops(run)] == ["Agent"]  # the main worker's own call; subagent words never


def test_narration_capped(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    for i in range(agents.NARRATION_MAX + 10):
        run.apply_claude(_text(f"step {i}", f"m{i}"), now=1000 + i)
        run.apply_claude(_tool("Bash", f"m{i}", f"t{i}", command="true"), now=1000 + i)
    assert len(run.data["narration"]) == agents.NARRATION_MAX
    assert texts(run)[-1] == f"step {agents.NARRATION_MAX + 9}"


def test_codex_narration_and_final_dropped_on_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "requests")
    run = agents.AgentRun(RID, "sess-1")
    run.start_main("Codex", "astra", now=1000.0)
    evs = [{"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Looking at the router."}},
           {"type": "item.started", "item": {"id": "i2", "type": "command_execution", "command": "ls"}},
           {"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "ls"}},
           {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "Done: all good."}}]
    for ev in evs:
        run.apply_codex(ev)
    assert texts(run) == ["Looking at the router.", "Done: all good."]
    assert ops(run) == [("command_execution", "Running: ls", "Ran: ls", "done")]
    run.finish_main(True)
    assert texts(run) == ["Looking at the router."]


def test_stopped_run_keeps_last_line(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.apply_claude(_text("Rendering the frames now.", "m1"))
    run.finish_main(False, stopped=True)
    assert texts(run) == ["Rendering the frames now."]


# ---- attached images -------------------------------------------------------------------------

def test_attach_inside_project_is_copied(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    shot = _png(proj / "shots" / "home.png")
    run = _run(tmp_path, monkeypatch, roots=[proj])
    run.apply_claude(_text(f"Took a screenshot.\nATTACH: `{shot}`", "m1"))
    run.apply_claude(_text("ATTACH: shots/home.png", "m2"))  # relative to the project, same bytes: once
    imgs = run.data["images"]
    assert len(imgs) == 1 and imgs[0]["name"] == "home.png" and imgs[0]["ext"] == "png"
    stored = agents.shot_path(RID, imgs[0]["id"], tmp_path / "requests")
    assert stored and stored.read_bytes() == shot.read_bytes()
    assert texts(run) == ["Took a screenshot."]  # the ATTACH line is not narration


def test_attach_rejects_outside_fake_and_non_images(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    outside = _png(tmp_path / "elsewhere" / "x.png")
    fake = proj / "fake.png"; fake.parent.mkdir(parents=True, exist_ok=True); fake.write_text("not an image at all", encoding="utf-8")
    txt = proj / "notes.txt"; txt.write_text("hello hello hello", encoding="utf-8")
    link = proj / "link.png"; os.symlink(outside, link)  # symlink out of the project
    run = _run(tmp_path, monkeypatch, roots=[proj])
    for p in (outside, fake, txt, link, proj / "missing.png", "../elsewhere/x.png"):
        run.apply_claude(_text(f"ATTACH: {p}", f"m-{p}"))
    assert run.data.get("images", []) == []
    assert agents.validate_image(str(outside), []) is None  # no roots: nothing allowed
    assert agents.validate_image(str(outside), ["/"]) is None  # a filesystem root is never a root


def test_attach_from_codex_message(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    shot = _png(proj / "out.png")
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "requests")
    run = agents.AgentRun(RID, "s"); run.attach_roots = [str(proj)]
    run.start_main("Codex", "astra")
    run.apply_codex({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": f"ATTACH: {shot}"}})
    assert len(run.data["images"]) == 1


def test_strip_attach_and_reply_body(tmp_path):
    assert agents.strip_attach("Done: x\nATTACH: /a/b.png\n\nVerified: y") == "Done: x\n\nVerified: y"
    assert agents.attach_paths("- ATTACH: '/a b/c.png'\n**ATTACH:** /d.jpg") == ["/a b/c.png", "/d.jpg"]
    assert "Attach: this" == agents.strip_attach("Attach: this")  # only the exact marker


def test_worker_rules_mention_attach():
    assert "ATTACH: /absolute/path.png" in providers.worker_rules("claude")
    assert "ATTACH: /absolute/path.png" in providers.worker_rules("codex", dynamic=True)


# ---- sidecar ---------------------------------------------------------------------------------

def _sidecar():
    spec = importlib.util.spec_from_file_location("cc_sidecar_narr", PKG / "sidecar.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_sidecar_payload_and_shot(tmp_path, monkeypatch):
    sc = _sidecar()
    reqs = tmp_path / "requests"
    monkeypatch.setattr(sc.common, "REQUESTS_DIR", reqs)
    monkeypatch.setattr(agents, "REQUESTS_DIR", reqs)
    proj = tmp_path / "proj"
    shot = _png(proj / "a.png")
    run = agents.AgentRun(RID, "s"); run.attach_roots = [str(proj)]
    run.start_main("Claude Code", "claude")
    run.apply_claude(_text(f"Here is the page.\nATTACH: {shot}", "m1"))
    run.apply_claude(_tool("Bash", "m1", "t1", command="true"))
    run.save(force=True)
    (reqs / RID / "request.json").write_text(json.dumps({"id": RID, "status": "executing"}), encoding="utf-8")
    row = sc._request_row(RID)
    assert row["narration"][0]["text"] == "Here is the page."
    assert [(n["kind"], n.get("status")) for n in row["narration"]] == [("text", None), ("op", "running")]
    assert row["narration"][1]["done"].startswith("Ran:")
    assert len(row["images"]) == 1 and set(row["images"][0]) == {"id", "name", "at", "agent"}
    assert str(proj) not in json.dumps(row)  # no paths on disk reach the browser
    body, ctype = sc.shot_bytes(RID, row["images"][0]["id"], "full")
    assert body == shot.read_bytes() and ctype == "image/png"
    assert sc.shot_bytes(RID, "0" * 16, "full") is None
    assert agents.shot_path(RID, "../../x", reqs) is None
    import shutil
    if shutil.which("sips"):
        thumb, tctype = sc.shot_bytes(RID, row["images"][0]["id"], "thumb")
        assert tctype == "image/jpeg" and thumb[:3] == b"\xff\xd8\xff"


def test_op_labels_scrubbed_and_settled_at_end(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    run.apply_claude(_tool("Bash", "m1", "t1", command="curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456' x"))
    assert "abcdefghijklmnop" not in json.dumps(run.data["narration"])
    run.apply_claude({"type": "result", "subtype": "success", "result": "ok"})
    assert ops(run)[0][3] == "done"  # nothing stays "running" after the run


def test_codex_file_change_and_failed_command(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "REQUESTS_DIR", tmp_path / "requests")
    run = agents.AgentRun(RID, "s"); run.start_main("Codex", "astra")
    run.apply_codex({"type": "item.completed", "item": {"id": "f1", "type": "file_change", "status": "completed",
                                                          "changes": [{"path": "/x/src/HeroDeck.tsx", "kind": "update"}]}})
    run.apply_codex({"type": "item.started", "item": {"id": "c1", "type": "command_execution", "command": "npm test"}})
    run.apply_codex({"type": "item.completed", "item": {"id": "c1", "type": "command_execution", "command": "npm test", "exit_code": 1}})
    assert ops(run) == [("file_change", "Editing HeroDeck.tsx", "Edited HeroDeck.tsx", "done"),
                        ("command_execution", "Running: npm test", "Ran: npm test", "failed")]


def test_codex_commands_show_without_the_shell_wrapper():
    import importlib
    ag = importlib.import_module("command_centre.agents")
    assert ag._codex_cmd("/bin/zsh -lc 'sleep 15'") == "sleep 15"
    assert ag._codex_cmd('bash -lc "npm test"') == "npm test"
    assert ag._codex_cmd("/bin/zsh -lc 'echo '\\''hi'\\'''") == "echo 'hi'"
    assert ag._codex_cmd("ls -la") == "ls -la"


def test_pdf_attach_becomes_file_card(tmp_path):
    proj, chat = tmp_path / "proj", tmp_path / "webui" / "attachments" / "abc123"
    (proj / "out").mkdir(parents=True)
    pdf = proj / "out" / "Q3 report.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 64)
    (proj / "fake.pdf").write_bytes(b"not a pdf at all, just text")
    text = f"Done: report\nATTACH: {pdf}\nATTACH: {proj / 'fake.pdf'}\nATTACH: /a/b.png"
    out = agents.attach_docs(text, [str(proj)], chat)
    assert f"MEDIA: {chat / 'Q3-report.pdf'}" in out and (chat / "Q3-report.pdf").read_bytes() == pdf.read_bytes()
    assert "(Couldn't attach fake.pdf: not a valid pdf.)" in out
    assert "ATTACH: /a/b.png" in out  # images stay for the Agents block
    assert agents.strip_attach(out).count("ATTACH") == 0
    outside = tmp_path / "elsewhere.pdf"; outside.write_bytes(b"%PDF-1.4\n" + b"y" * 64)
    assert "outside the project folders" in agents.attach_docs(f"ATTACH: {outside}", [str(proj)], chat)
    # a live ATTACH of a PDF is not an image and not a "couldn't show" either
    run = agents.AgentRun(RID, "s"); run.attach_roots = [str(proj)]
    run.start_main("Claude Code", "claude")
    assert run.attach(str(pdf), run.main(), 1.0) is False
    assert not run.data.get("images") and not run.data.get("attach_rejected")


def test_worker_rules_mention_pdf():
    assert "ATTACH: /absolute/path.pdf" in providers.worker_rules("claude")


def test_any_file_type_attach_becomes_file_card(tmp_path):
    proj, chat = tmp_path / "proj", tmp_path / "webui" / "attachments" / "abc123"
    proj.mkdir(parents=True)
    for name, data in (("notes.md", b"# Hi\n"), ("data.csv", b"a,b\n1,2\n"), ("build.zip", b"PK\x03\x04" + b"z" * 40),
                       ("deck.pptx", b"PK\x03\x04" + b"p" * 40), ("tiny.txt", b"x")):
        (proj / name).write_bytes(data)
    text = "\n".join(f"ATTACH: {proj / n}" for n in ("notes.md", "data.csv", "build.zip", "deck.pptx", "tiny.txt"))
    out = agents.attach_docs(text, [str(proj)], chat)
    for n in ("notes.md", "data.csv", "build.zip", "deck.pptx", "tiny.txt"):
        assert f"MEDIA: {chat / n}" in out and (chat / n).read_bytes() == (proj / n).read_bytes()
    # same name, different content: both kept
    (proj / "sub").mkdir(); (proj / "sub" / "notes.md").write_bytes(b"# Other\n")
    out2 = agents.attach_docs(f"ATTACH: {proj / 'sub' / 'notes.md'}", [str(proj)], chat)
    assert "MEDIA: " in out2 and "notes-" in out2


def test_secret_files_are_never_attached(tmp_path):
    proj, chat = tmp_path / "proj", tmp_path / "chat"
    proj.mkdir()
    for n in (".env", ".env.local", "id_rsa", "server.pem", "api.key", "aws-credentials.json"):
        (proj / n).write_bytes(b"SECRET=1\n" * 4)
        out = agents.attach_docs(f"ATTACH: {proj / n}", [str(proj)], chat)
        assert "looks like a secret or key file" in out, n
        assert not (chat / n).exists()
    assert agents.is_doc("/x/y.md") and not agents.is_doc("/x/y.png")
