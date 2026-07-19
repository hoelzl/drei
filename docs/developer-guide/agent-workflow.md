# Multi-harness agent workflow

`AGENTS.md` is the shared always-on contract. Claude Code uses the thin `CLAUDE.md` adapter; Codex, Hermes, and other harnesses should discover `AGENTS.md` directly. Do not duplicate procedures in vendor directories.

Use one branch and one external sibling worktree per concurrent writer. Give each agent the exact worktree path and task boundary. The primary checkout remains the clean integration point. Before handoff, provide the candidate commit/diff, focused and full gate output, known risks, and documentation impact.

Durable facts and accepted plans belong under `docs/`; volatile progress belongs in Git/GitHub state. Add a canonical `.agents/skills/<name>/SKILL.md` only after a recurring workflow has been exercised and reviewed. Harness-specific wrappers may point to it but may not copy it.
