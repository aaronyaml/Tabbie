"""hmem — Hermes institutional memory.

Files are the API (Markdown + frontmatter under ~/hermes/memory). This package enforces what plain
files can't: atomic optimistic-concurrency writes, scopes and permissions, provenance, git history,
proposals + approval, poisoning/secret controls, traces, extraction and the Dreamer.
See ~/hermes/docs/memory/ARCHITECTURE.md and ~/hermes/memory/README.md.
"""
