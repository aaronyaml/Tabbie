# Hermes Projects + project-aware Dreamer

Status: implemented (2026-11-01). This file describes what the code does; code wins if they differ.

## 1. What exists and is reused

| Existing piece | Role today | Reuse |
|---|---|---|
| `hmem` store (`~/hermes/memory`, own git repo) | scoped memories `projects/<key>/`, lock + OCC + commit, proposals, traces, Dreamer | project registry and project memory live here; same lock, same commits, same proposals |
| `trace.project_name(path)` | maps a cwd to a project key (folder name) | becomes registry-aware: any path inside a linked folder → that project's id |
| `command-centre/projects.py` `resolve()` / `folders_for()` | per-request project + extra folders; multi-folder "workspaces" as symlink dirs `~/hermes/workspaces/<name>` | registry projects get the same symlink workspace dir, so the WebUI picker, `resolve()` and `--add-dir` all work unchanged |
| WebUI native projects (`/api/projects/create`, `bind`, `newSession({project_id, workspace})`) | sidebar grouping + "+" opens a session in the bound workspace | each Hermes project gets one native WebUI project bound to its workspace dir; New task = `newSession` |
| cc-agents sidecar (127.0.0.1:8794, proxied by the WebUI behind its login + per-extension token) | read-only agent state | gains `/projects/*` endpoints: the only path from the browser/iPhone to the registry |
| Motion studio panel pattern (rail button → full panel) | UI | Projects panel uses the same pattern and the Apple-skin tokens |

Not reused: the old auto-discovered `projects:` list (one entry per repo). It stays for routing
of folders that belong to no project, but a repo never becomes a Project automatically.

## 2. Project model

`memory/projects/<id>/project.json` (versioned in the memory repo; written under the store lock with
an `expected_hash` check, then committed):

```json
{"schema": "hermes.project/1", "id": "acme-3f9a", "name": "Acme", "slug": "acme",
 "description": "", "status": "active|archived", "created_at": "...", "updated_at": "...", "version": 3,
 "primary_folder": "f-1a2b", "folders": [
   {"id": "f-1a2b", "path": "/Users/me/acme-app", "label": "acme-app", "added_at": "...",
    "git": {"root": "...", "remote": "github.com/me/acme-app"},
    "context_files": ["CLAUDE.md", ".hermes/architecture.md"]}],
 "instructions": "user-written, <= 16 KB", "webui_project_id": "12hex", "memory_keys": [],
 "dreamer": {"after_sessions": 10}}
```

- `id` = slug at creation + 4 random hex; never changes on rename. Memory, traces, proposals and
  dream runs reference the id, never the name.
- Memory root `memory/projects/<id>/` (flat files, `type` says architecture/decision/workflow/gotcha/…);
  skills `memory/projects/<id>/skills/<name>/SKILL.md`; sessions are traces whose `project` = id.
- Branch, HEAD and dirty state are read live (`git_live`) for the UI, never stored; the remote is
  stored with credentials stripped. `INSTRUCTIONS.md` is written beside `project.json`.
- Legacy folder-keyed memory (`projects/acme/` from before) is never moved. The project home
  lists candidates (`legacy_candidates`); the user opts in, which adds the key to `memory_keys`
  (an alias the project can read). A key belongs to at most one project.

## 3. Folder policy (the Mac's filesystem, reached from any device)

- Only via the sidecar, which is only reachable through the WebUI login (and Tailscale/LAN Caddy).
- `realpath` everything; must be an existing directory under `allowed_root()` (`HERMES_PROJECT_ROOT`,
  default `$HOME`); not `$HOME` itself, not inside any hidden folder (`~/.ssh`, `~/.hermes`, …), not a
  personal top-level folder (Library, Applications, Desktop, Documents, Downloads, Movies, Music,
  Pictures, Public, iCloud), not Hermes itself or any folder containing it.
- Attaching through the API is two-step: `/fs/check` returns the canonical path, and
  `/projects/folder/add` needs `confirm_path` equal to it (no symlink swap between check and attach).
  `folder_ok()` re-checks every folder when it is used.
- One folder belongs to at most one active project; no duplicates and no nesting (a folder inside
  or containing an attached one is rejected with the reason).
- Browsing returns sub-directory names only (no files, no hidden dirs), same restrictions.
- Repo-local context files read for the UI are a fixed allowlist (`CLAUDE.md`, `AGENTS.md`,
  `.hermes/context.md`, `.hermes/architecture.md`, `.hermes/decisions/*.md`, `.hermes/workflows/*.md`),
  size-capped and secret-scrubbed.

## 4. Task context (progressive disclosure)

A project request's worker prompt gets ~15 lines, never bodies:
identity (name, id, one-line description) → folders (label, path, primary, git, which have local
context files) → the folder that best matches the request → project instructions (inline if ≤ 1200
chars, else path) → project skills (name + description + path) → memory map + ≤ 5 relevant titles from
global + this project only. Claude runs in the primary folder with the others as `--add-dir`.
"Code beats memory; if memory is wrong, `hmem propose modify`" is part of the memory line.

## 5. Traces

Unchanged builder, plus `attribute()` at save: `project` = registry id, `project_id`, `folders` =
linked folders the run touched (cwd, files read/modified). A trace never moves between projects.
Older unowned traces are claimed once, when a project is created or a folder attached
(`claim_history`, logged as `trace_claimed`). Secrets scrubbed as before.

## 6. Dreamer, project mode

`hmem dream --project <id>`: traces filtered to the project, findings and proposals tagged with it,
run stored as `dreams/<cycle>/` with `project`. Specialists (existing + 2 new, all deterministic):
failures, feedback, hygiene (memory quality, scoped to the project's keys), tools,
rediscovery (documentation gaps), workflows (skill candidates), **cross_repo** (same error in ≥ 2
linked folders, same-named modules edited in several folders; report-only, never a proposal),
**conflicts** (project memories whose repo-relative backticked paths are gone from every linked
folder while their top directory still exists → deprecate proposal; skipped while any folder is
missing and for memories older than the last folder removal; this evidence is the code itself, so it skips the session-support gate). The synthesiser gates on support (≥ 3 conversations, ≥ 2 days), caps,
suppression and trust, as today. Findings carry `relevant_sessions`, `affected_sessions`,
`confidence`, `recommended_action {type, target}`. Each scope has its own lock
(`dream-<project|global>.lock`) plus a shared `collect.lock`; pending proposals are capped at 15 per
project and 25 outside projects. A global run strips absolute paths from evidence that came from
registered projects, so project details don't leak into global memory.

Auto-approval: `proposals.auto_approvable(p, cfg)` exists and is tested but returns False unless
`memory.auto_approve` is true in `config/router.yaml` and the type is in `auto_approve_types`, trust
is agent/user, risk low, scope is a project, and the scanner found nothing. Off by default; global,
skills and deprecate never. Nothing calls it with the policy on yet.

Triggers (`TRIGGERS` registry): manual (UI/CLI), after N project sessions (the project's
`dreamer.after_sessions`), nightly (LaunchAgent `ai.hermes.dreamer`: global + each project with new
traces). Idle-time is a registered stub. Every trigger spawns a detached, nice'd process, so a
dream never blocks a task; a scope that is already dreaming is skipped.

## 7. UI

Rail entry "Projects" → panel. List (cards: name, folders, sessions, pending) + New project sheet.
Project home: header with counts, primary repo, last dream, [New task] [Run Dreamer]; tabs Folders,
Sessions, Memory, Dreamer (runs, findings, proposals with evidence + diff, approve/reject), Skills,
Instructions, Architecture (architecture/decision memories + repo-local docs), Settings (rename,
description, archive/delete). Folder picker = server-side directory browser + "known repos" list.

New task: the panel creates (once) a WebUI native project bound to `~/hermes/workspaces/<id>/`
(symlinks to each folder) and calls `newSession(false, {project_id, workspace})`. The command centre
maps that cwd (or an explicit `@project:<id>` marker) back to the registry project, runs the worker in
the primary folder with the others as `--add-dir`, and puts `projects.context_block()` in the prompt.

## 8. Security notes

- The browser reaches the registry only through the sidecar, behind the WebUI login and the
  per-extension token. Workers are denied `Read` on the token directory and file-tool writes on the
  memory store; memory changes only through proposals.
- `/projects/file` serves only the context-file allowlist from linked folders, 64 KB, secret-scrubbed.
- Proposal approve/reject check that the proposal belongs to the project in the URL.
- Repo text, instructions and traces are data: instructions are nonce-delimited in prompts, and
  scanner flags (`security.scan`) must be confirmed in the UI before approval.

- Paths are compared in their on-disk spelling (`true_case`): APFS ignores case, so `~/LIBRARY` or
  `~/HERMES/memory` can't slip past the policy, and one folder can't join two projects in two spellings.
- Folder labels and context-file names must be plain one-line names; they are cleaned again before
  they reach a prompt. `git status` on linked repos runs with fsmonitor, hooks and the pager disabled.
- `project.json`, `INSTRUCTIONS.md` and project skills reach a prompt only if they match what the
  store committed; otherwise instructions are withheld and the mismatch is logged.
- Workers run with `HERMES_WORKER=1`. `hmem approve|reject|deprecate|doctor|migrate` and project
  edits refuse to run there, and `hmem add` from a worker becomes a proposal. A worker can still
  unset the variable or read the sidecar token with Bash: this is defence in depth, not a sandbox.
- Proposal signatures, rejections (30-day suppression) and "already approved" checks are scoped to
  the project (or global). A project proposal may only target that project's memory keys, and
  ownership is taken from the target. Proposal creation is one locked check-then-save.
- A global dream leaves a single registered project's pattern to that project's Dreamer; patterns
  shared by several projects become global with paths, file names and project/folder names removed.
- Inside a project's session only a leading `@project:<name>` switches project; "in project x" or a
  pasted `[x]` stays plain text. An archived project's workspace asks which project to use.

## 9. Implementation map

| Piece | Code |
|---|---|
| Registry, folder policy, context block | `hmem/projects.py` |
| HTTP API | `hmem/api.py` (served by `plugin/command-centre/sidecar.py`) |
| Trace stamping | `hmem/trace.py` `attribute()` |
| Isolation in retrieval | `hmem/retrieve.py` `project_keys()` |
| Dreamer project mode, triggers | `hmem/dreamer.py` |
| Proposals (project, diff, auto policy) | `hmem/proposals.py`, `hmem/skills.py` |
| Routing + worker prompt | `plugin/command-centre/__init__.py`, `projects.py` |
| CLI | `hmem project …`, `hmem dream --project <id>` |
| UI | `webui-extension/projects.js`, `projects.css` |
| Tests | `tests/test_projects.py` |

## 10. Known limits

- Git diffs are not stored in traces; only file lists and commands.
- The idle trigger is a stub; auto-approval is designed and tested but off.
- A worker could still read the sidecar token with Bash; the deny rule covers the Read tool only.
- `GET /projects` rebuilds each card from all pending proposals and memories (fine at today's size).
