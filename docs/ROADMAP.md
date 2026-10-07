# Roadmap

Phases are ordered by dependency, not by date. Each phase ends with a short review: what was learned, which decisions changed, which experiments are next.

## Phase 0 — Research and architecture ✅ (approved 2026-10-07)

This document set.

## Phase 1 — Walking skeleton: one agent, fully observed ✅ implemented 2026-10-07 (awaiting review)

### Status against the stop conditions

| Condition | Status |
|---|---|
| One agent runs end to end | IMPLEMENTED; verified offline with a scripted model. Live run NOT YET VERIFIED (no API key) |
| At least one tool works | IMPLEMENTED: `list_files`, `read_file`, `write_file` |
| Permission denial works | IMPLEMENTED: policy deny and human deny, both journaled |
| Journal records the execution | IMPLEMENTED: 18 event types, append-only triggers |
| Trace works | IMPLEMENTED: `agent-lab trace`, `show`, `runs` |
| Replay semantics tested/documented | IMPLEMENTED: `agent-lab replay`; E010 run offline |
| Tests pass without an API key | 84 passed, 2 opt-in skipped, 1 live deselected |
| E000 exists | script + offline test; live run pending |
| E001 structure exists | task, checks, runner; live runs pending |
| E010 exists | done (offline) |
| Budget controls exist | turns, tool calls, tokens, estimated cost with worst-case pre-check |
| Secrets protected | `.env` git-ignored; key never in request bodies; journal redacts registered secrets (tested) |
| Docs reflect reality | status banners in each doc; decisions D-018…D-025 |

What was actually built differs from the plan below in these ways: no `complete_task` / `request_capability` tools (D-019, D-022), no Pydantic or rich (D-021), synchronous loop (D-020), layout is flat `src/agentlab/*.py` (12 small modules) instead of sub-packages, one task bank seed of two tasks instead of ~10, tasks carry the agent config instead of separate `specs/` and `policies/` files.

### Original plan

**Goal:** one agent completes real tasks in a disposable workspace, through the kernel, with every effect journaled, permissions enforced, and the run replayable offline.

**Prerequisites (owner):** an Anthropic API key in `.env`; approval to `git init` this directory; a spending cap for Phase 1 (suggested: a small fixed amount set in the Console, plus the kernel's run cap).

### Steps

| # | Step | Output | Learning focus |
|---|---|---|---|
| 1.0 | **E000 naked loop**: ~60 lines, one tool, raw JSON printed at every step, no abstractions | `experiments/000-naked-loop/` | What `tool_use`/`tool_result`/`stop_reason` really are; how thinking blocks appear |
| 1.1 | Project scaffold: `uv` project, Python 3.14, `anthropic`, `pydantic`, `rich`, `pytest`, `ruff`; `.gitignore` for `runs/` and `.env` | `pyproject.toml`, `src/agentlab/` | Dependency hygiene |
| 1.2 | Live smoke test of beta features we rely on (`display: "updates"`, fallbacks, prefix-check controls) | `tests/live/` (opt-in) | Verify RESEARCH §7 items 4–5 |
| 1.3 | Event envelope, journal (SQLite), blob store, projections skeleton | `kernel/journal.py`, `kernel/events.py` | Event sourcing |
| 1.4 | Model Gateway: request building, prefix-invariant check, retries, refusal/max_tokens handling, record + replay modes | `model/gateway.py`, `model/cassette.py` | The API contract; determinism boundary |
| 1.5 | Tool registry, ToolSpec, path-confined workspace tools, `complete_task`, `request_capability`; adversarial path tests | `tools/` | Syscalls; Windows path pitfalls |
| 1.6 | Capabilities + policy engine + CLI approval gate | `policy/` | Default deny, attenuation, approvals |
| 1.7 | Agent runtime loop + scheduler limits (turns, tool calls, tokens, time, repetition) | `agents/runtime.py` | What makes a loop an agent; runaway guards |
| 1.8 | CLI: `lab run`, `lab trace`, `lab show`, `lab replay`, `lab export` | `cli.py` | Reading a run |
| 1.9 | Seed task bank (~10 tasks in T1, T3, T4) with checkers | `tasks/` | Verifiable tasks |
| 1.10 | **E001** single-agent baseline + **E010** replay determinism | `experiments/001-…`, `010-…` | First real data |

Proposed layout:

```
AI-Agent-Lab/
  src/agentlab/
    kernel/    journal.py  events.py  projections.py  scheduler.py
    model/     gateway.py  cassette.py
    tools/     spec.py  registry.py  workspace.py  kernel_tools.py
    policy/    capabilities.py  engine.py  approval.py
    agents/    spec.py  runtime.py
    cli.py
  specs/       agent specs (TOML)
  policies/    policy files (TOML)
  tasks/       task bank (fixtures + checkers)
  experiments/ NNN-name/
  tests/       unit/  replay/  live/ (opt-in)
  runs/        lab.db, workspaces/  (git-ignored)
```

### Exit criteria

- A task from the bank runs end-to-end via `lab run` and finishes through `complete_task`.
- Every model call, tool call, permission decision, approval, and file write appears in `lab trace`.
- A denied write and a `request_capability` approval are demonstrated and journaled.
- `lab replay <run>` reproduces a recorded run with byte-identical model requests and no API calls; this is a test in CI.
- Adversarial path tests pass. Test suite runs offline.
- E001 and E010 written up with Observed / Inferred / Not yet verified sections.

### Not in Phase 1

Delegation, multiple agents, code execution, sandbox, memory beyond the workspace, MCP, web access, web UI, OTel.

## Free-first core refactor ✅ implemented 2026-10-07 (MODEL-GATEWAY.md steps 1-6)

Provider-neutral internal types, scripted backend, loop/kernel/gateway/budget/replay on neutral types, journal schema v2, `anthropic` moved to an optional extra. Offline guarantees are tested (no SDK, no key, no network). Next: step 7, the local `OpenAICompatBackend` for llama.cpp `llama-server` (not started; nothing installed or downloaded).

## Phase 2 — Multi-agent and sandboxed execution

- `delegate` (synchronous, concurrent within a turn), task tree, capability attenuation, budget carving, cycle/depth/fan-out guards.
- Code-defined pipelines and an LLM orchestrator spec.
- Sandbox backend (Docker in WSL2) and `run_python`; experiments S-01, S-02.
- Failure injection (chaos mode), fork from journal.
- Experiments E002, E003, E004, E007, E009, E013, E014, E018.

## Phase 3 — Agent Observatory

- Kernel API server (localhost), SSE event stream, REST commands.
- Frontend: swimlane timeline, task tree, tool-call inspector, per-step context viewer, run diff, approval queue, replay/fork controls.
- Detailed UI design happens at the start of this phase, from the questions Phase 1–2 transcripts actually raised.

## Phase 4 — Memory and evaluation

- Long-term memory (memory tool backed by the journal), provenance, snapshots; E005.
- Experiment runner (variants × tasks × repetitions, budgets, result tables); evaluate Inspect AI as the harness.
- Episodic `search_history` tool.

## Phase 5 — Frontier experiments

Async delegation (E017), peer messaging and blackboard orchestration (E011), MCP tool adapter, web tools with the trifecta rule (E012), stronger isolation (S-03 gVisor, S-04 microVM, S-05 sandbox-runtime), OTel export, Managed Agents as a comparison baseline.
