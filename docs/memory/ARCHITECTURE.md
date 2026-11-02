# Hermes institutional memory + Dreamer

Status: implemented (see "Implementation map"). This file is the design record: the audit of
what existed, the target, the gaps, and the migration.

## 1. Starting point

Before this design the command centre already routed every turn, kept request records on disk and
streamed worker events, but memory was flat (unscoped fragments, no provenance, no versioning),
Claude Code's own auto-memory was siloed per working directory, traces were not sanitised or
assembled, and nothing learned from past sessions, so the same mistakes repeated. Global
`CLAUDE.md` files tended to grow into large knowledge dumps loaded into every session.

## 2. Target architecture

```
ASTRA (plan) → HERMES (route) → Claude workers (+subagents) → work + tool calls
                                         │
                       request finishes  ▼
                     TRACER  (sanitised trace + outcome, memory/sessions/…)   ← evidence
                                         │
                     EXTRACTOR (cheap, deterministic signals; optional LLM when idle)
                                         │  candidates → proposals (never direct shared writes)
                     DREAMER (offline, many sessions) ── specialists ── synthesiser
                                         │  evidence-backed proposals (support ≥ N sessions)
                     VALIDATION (schema, poisoning scan, conflict check) → APPROVAL (user)
                                         │
                     STORE (git-versioned Markdown, scopes, OCC, permissions)
                                         │
                     RETRIEVAL (progressive disclosure: map → index → search → read)
                                         └──────────► future worker runs
```

Principles:
- **Files are the API.** Memory = Markdown + frontmatter. Agents use `rg`/`find`/Read. The
  `hmem` library/CLI exists only for the parts that must be enforced: atomic OCC writes,
  permissions, provenance, versioning, proposals.
- **Progressive disclosure.** Workers get ~6 lines: where memory lives, how to search it, and the
  *titles* of the few entries that look relevant for this project. Never bodies by default.
- **Separation of duties.** Worker does the task. Tracer/extractor run after the task, outside
  the worker's context. Dreamer runs offline across sessions. Only validated + approved
  proposals change shared memory.
- **Code is the source of truth.** Memory that references paths/symbols that no longer exist is
  flagged stale by the Dreamer and a correction proposal is generated.

## 3. Storage layout (`~/hermes/memory/`, its own git repo)

```
memory/
  README.md              the map (what exists, how to search, rules)  ← linked from CLAUDE.md / worker prompt
  INDEX.md               generated: one line per active memory, grouped by scope
  global/                universal: principles, user preferences, engineering, workflows
  projects/<project>/    per-project knowledge (isolated: only loaded when that project is in play)
  agents/<agent-id>/     per-agent notes (e.g. agents/astra, agents/motion-worker)
  proposals/{pending,approved,rejected}/<id>.json   (+ <id>/SKILL.md for skill proposals)
  dreams/<cycle-id>/     report.md + findings.json per dream cycle
  archive/               deprecated/superseded bodies (never hard-deleted)
  sessions/YYYY/MM/<id>/ trace.json + summary.md + lessons.json   (gitignored: evidence, not knowledge)
  .stats/                usage.jsonl, retrievals.jsonl, events.jsonl (gitignored, append-only)
  .locks/                short-lived OCC locks (gitignored)
```
Scratch = the request's own `state/requests/<id>/work`, never persisted as memory.

## 4. Memory document schema

```markdown
---
id: projects/acme/auth-ownership
scope: project            # global | project | agent | session
project: acme        # required for scope=project
agent:                    # required for scope=agent
title: Supabase auth is owned by the API, not the app
description: one line used for search and the index
type: fact                # fact | preference | rule | procedure | decision | reference | lesson
status: active            # active | deprecated | superseded | archived
version: 3
created_at: 2026-11-01T10:00:00Z
updated_at: 2026-11-01T10:00:00Z
created_by: dreamer        # actor id
approved_by: user          # who approved the write (shared scopes)
source_sessions: ["20261029-185606-ab12cd"]
evidence: ["session 20261029-…: …"]
trust: user                # user | verified | agent | external
confidence: 0.9
supersedes: []
superseded_by:
tags: ["supabase", "auth"]
last_validated: 2026-11-01
refs: ["~/acme/api/auth.ts"]   # code it describes; staleness checks resolve these
---
body (≤ 2 KB; one concept per file)
```
Usage (`last_used`, `usage_count`) is kept in `.stats/usage.jsonl`, not in the file, so reads never
create git churn or write contention.

## 5. Scopes, permissions, trust

| Scope | Direct write | Otherwise |
|---|---|---|
| global | `user` only | proposal → user approval |
| project | `user`, `manager:<project>` | proposal |
| agent | `user`, that agent | proposal |
| session | anyone | — |

Actors: `user`, `hermes`, `manager:<p>`, `worker:<request>`, `agent:<id>`, `extractor`, `dreamer`,
`proposal:<id>` (an approved proposal applies as the approver). Workers and the Dreamer can
never write shared scopes directly; their writes become proposals automatically.

Trust: content derived from web pages, MCP or downloaded files is `external`; the proposal's
trust is the minimum over its evidence. `external` content can never auto-apply, and
the poisoning scanner (instruction-like text, exfil URLs, shell pipes, hidden unicode, secrets)
blocks it outright unless a human edits it. Memory bodies are injected as quoted data with a
"reference, not instructions" header.

## 6. Concurrency

Optimistic concurrency per document: readers get `sha256(content)`; writes pass
`expected_hash`. Under a short per-document `flock`, the store re-hashes; mismatch →
`ConflictError(current)` and the caller reloads/reconciles. Writes are atomic (tmp + fsync +
rename). Git commits run under one repo-level lock. Append-only JSONL stats use `O_APPEND`
single-write lines (atomic for < PIPE_BUF).

## 7. Session tracing

After every request (`_execute`/takeover finish) a background thread builds
`sessions/YYYY/MM/<request-id>/trace.json` from: request.json, agents.json, the decision log,
Claude's transcript slice for that run (tool calls, results truncated, errors, retries, files
read/modified, commands, test runs), git diff stat, outcome, user feedback (the next user message
in the same conversation, classified), tokens/cost/latency when present. Everything passes the
secret scrubber first. A backfill command builds traces for historical requests and for direct
Claude Code sessions (interactive work counts as fleet history too). WebUI hub chats are a third
source (`trace.build_webui`): the user's own messages, attributed to a project by the chat's
workspace, its WebUI project, or the one project its worker requests ran in.

## 8. Learning loop

- **Extractor (per task, cheap):** deterministic signals only (failure, retries, tool errors,
  correction-style feedback, long runs). Produces `lessons.json` *candidates* in the session dir.
  It never writes shared memory and most sessions produce nothing.
- **Dreamer (offline):** loads traces since the last cycle + all memory. Specialists:
  1. failures (error-signature clusters), 2. tools (per-tool/command failure rates),
  3. feedback (user corrections → preference patterns), 4. rediscovery (same files/facts read
  across many sessions → doc gap), 5. workflows (stable successful tool sequences → skill
  candidates), 6. memory hygiene (duplicates/conflicts/staleness vs code), 7. security (poison
  scan of memory + pending proposals). Each emits findings with support counts and evidence.
  The synthesiser keeps only findings with support ≥ `min_support` sessions across ≥ 2 distinct
  days (configurable) and turns them into proposals.
  8. stated (`spec_stated`): things the user tells an agent outright ("I like…", "we deploy on…",
  "always…/never…"). One statement is enough; support counts don't apply. A regex prefilter picks
  candidate sentences; a Haiku pass (`dream_stated_llm`, subscription only, cached per sentence,
  capped per cycle) classifies them as preference / fact / rule and titles them; `verify_stated`
  rejects any title whose words don't come from the sentence, and anything sensitive or flagged by
  the poison scan. Each becomes a proposal with the quote as evidence. Never auto-approved. An optional LLM pass (claude -p on the
  subscription, never an API key) only *phrases* proposals from the evidence; it cannot add
  findings.
- Triggers: manual (`hmem dream`), after N new traces, nightly LaunchAgent. Dreaming never runs
  while a worker request is executing (checks `state/requests`), never blocks requests.

## 9. Skills

Skill proposals carry a full `SKILL.md`. Validation: frontmatter (name, description), size,
no secrets, poison scan, references resolve. Approval installs into `~/.claude/skills/<name>/`
(backup of any existing skill first). Nothing deploys without explicit approval.

## 10. Retrieval (progressive disclosure)

1. Worker prompt: memory map pointer + up to 5 *titles/ids* for the resolved project and global
   scope (never other projects).
2. Worker searches when relevant: `hmem search "<q>" --project <p>` or plain `rg` over
   `memory/global memory/projects/<p>`.
3. Reads the one or two files that matter.
Hub turns keep fragment-memory's recall, now scope-filtered and status-filtered (active only).
Deterministic BM25 over title/description/tags/body. No vector DB: the corpus is hundreds of
small files; revisit only if eval recall drops.

## 11. Gaps closed

provenance, scopes, isolation, permissions, proposals, versioning, OCC, poisoning guard,
lifecycle, traces, extraction, cross-session analysis, skill discovery, observability, evals,
migration of Claude auto-memory (64 mirrors, 2 quarantined), `~/hermes/CLAUDE.md` as map.

## 12. Migration

- Claude auto-memory → `hmem migrate`: each file becomes a **mirror** (`mirror_of: <path>`, `trust: agent`,
  `created_by: migration`), searchable and analysed by the Dreamer but not injected (Claude Code already
  loads the original, which always wins). Idempotent via a `src-<hash>` tag. Folder → project name,
  including folders that no longer exist. Files the scanner blocks become pending proposals instead.
- `~/.hermes/memories/USER.md` → global mirror.
- fragment-memory tools keep their names; they now read/write the hmem store.

## 13. Review-driven decisions (architecture, security, systems, continual-learning reviews)

- Trust is three levels (`external < agent < user`); nothing raises trust. External memory may only be
  `fact`/`reference`, never global, never injected. Non-user approvals wait 24 h (`inject_after`).
- One store lock covers write + rename + commit. Integrity: a file is trusted only if its blob equals
  HEAD's; `hmem doctor` reports hand edits.
- Support = distinct conversations (≥3) on distinct days (≥2) plus an origin-independence check, so one
  poisoned page repeated in a loop can't manufacture consensus.
- Tools/rediscovery/workflows specialists are report-only (too noisy to propose). Generic errors
  (FileNotFound, JSONDecodeError, KeyError, safety-check denials) are report-only too; a failure lesson
  is `external` only if web text leaked into its evidence.
- Caps: ≤25 pending, ≤5 per cycle, rejected signatures suppressed 30 days.
- Optional LLM phrasing (off by default) runs Claude with no tools/MCP in an empty dir and is rejected
  unless every quote and path it uses appears in the evidence.
- Generated skills are namespaced `hermes-gen-*`, command-allowlisted, validated, and installed only on approval.
- Eval metrics: retrieval recall@3 (tests), recurrence of approved failure signatures, uptake of offered titles.

## 14. Implementation map

| Module | Does |
|---|---|
| `hmem/doc.py` | frontmatter parse/render/validate, trust order |
| `hmem/security.py` | `scrub` (secrets), `scan` (poisoning), safe refs, prompt quoting |
| `hmem/store.py` | scopes, `can_write`, locked atomic OCC writes + git commit, history/rollback, integrity, index |
| `hmem/retrieve.py` | BM25 search, worker map + ≤5 titles, hub recall |
| `hmem/proposals.py` | create/approve/reject, stale detection, suppression, caps |
| `hmem/trace.py` | sanitised traces from Hermes requests and Claude Code transcripts, taint, outcome |
| `hmem/extract.py` | post-request extraction (explicit rules, corrections, resolutions), Dreamer trigger |
| `hmem/dreamer.py` | specialists, gate, synthesis, report, backup, triggers |
| `hmem/skills.py` | generated-skill validation/install |
| `hmem/stats.py` | observability + eval metrics |
| `hmem/migrate.py` | auto-memory mirrors |
| `hmem/cli.py`, `bin/hmem` | CLI |
| `plugin/command-centre` | memory context in worker prompts, `_learn` after each request, worker deny rules |
| `plugin/fragment-memory` | hub tools on hmem |
| `services/ai.hermes.dreamer.plist` | nightly cycle 03:30 |
| `tests/test_hmem.py`, `tests/test_memory.py` | store, OCC, permissions, poisoning, Dreamer, evals |
