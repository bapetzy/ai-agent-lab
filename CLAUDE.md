# AI-Agent-Lab — working notes for Claude

Personal research lab (BAPE-LAB, AI agent rabbit hole). Parent lab and its rules: `..\BAPE-LAB\CLAUDE.md`. Owner is a learner; the goal is understanding, not shipping.

## Current state

- Phase 0 approved; Phase 1 (one agent, fully observed) implemented 2026-10-07 and awaiting owner review. Do NOT start Phase 2 (delegation, sandbox, UI) without explicit approval.
- Decisions live in `docs/DECISIONS.md`. Change a decision there (new superseding entry) before changing the code that depends on it.
- Commands: `uv run pytest` (offline), `uv run pytest -m live` (costs money), `uv run agent-lab --help`.
- Docs label parts IMPLEMENTED / PROPOSED / NOT YET VERIFIED. Keep that true when code changes.

## Rules

- Never invent APIs, SDK behaviour, or security guarantees. Claude API details: use the `claude-api` skill or official docs. Label claims Observed / Inferred / Not yet verified.
- Complexity needs a written reason (a decision entry or an experiment that needs it).
- Explain the underlying concept when introducing a mechanism; do not hide it behind an abstraction.
- Security: agents are untrusted. No agent gets unmediated filesystem, process, or network access. Never put `ANTHROPIC_API_KEY` or other secrets inside a sandbox, workspace, tool output, or the journal. Fallback to another model stays disabled unless a new decision says otherwise (D-018).
- Destructive or system-changing commands on the host (WSL config, Docker, firewall, deleting files): explain and ask first.
- Experiments follow the parent lab format: Question, Hypothesis (written before running), Setup, Procedure, Evidence, Interpretation, Uncertainty, New Questions.

## Environment (observed 2026-10-07)

Windows 11 Home, 5.8 GB RAM, Ryzen 7 5825U. Python via `uv` (3.14 and 3.11 available), Node 24. WSL2 Ubuntu 24.04 with Docker Engine 29.7 and `/dev/kvm`. No Docker Desktop, no Windows Sandbox (Home edition). No API key configured yet.
