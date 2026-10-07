# Memory

> **Implementation status (Phase 1).** Only M0 (the transcript, append-only) and M2 (files in the per-run workspace) exist. Nothing persists between runs except the journal, which agents cannot read. No compaction, no memory tool, no long-term memory.

This document answers: how memory differs from context, what should be persisted, how memory should work for agents in this lab, and what can go wrong.

## 1. Context is not memory

- **Context** is what the model sees in one request: system prompt, tool definitions, and the transcript. It is the agent's working memory. It disappears when the instance ends, and it is bounded (1M tokens on Opus 5.5, but cost and attention degrade long before that).
- **Memory** is anything that outlives an instance and can be brought back into some future context. The model has no memory of its own between calls; every form of memory is something the harness stores and later puts back into a request.

So "memory" is always two operations owned by the harness or exposed as tools: **write** (what to keep, from where) and **recall** (what to put back, when, and how much). Both are design decisions, and both are events in this lab.

## 2. Memory tiers

| Tier | What | Lifetime | Who writes | Who reads | Phase |
|---|---|---|---|---|---|
| **M0 Working context** | The instance's transcript | One instance | Kernel (append-only) | The model, every turn | 1 |
| **M1 Handoff** | Task brief + referenced inputs passed at delegation | One task | Delegating agent | Child agent (first message) | 2 |
| **M2 Workspace** | Files in the run workspace (notes, drafts, data) | One run | Agents via `write_file` | Agents via `read_file` | 1 |
| **M3 Episodic** | The journal of past runs | Permanent | Kernel | Humans (Observatory); later agents via a read-only `search_history` tool | 4 |
| **M4 Long-term semantic** | Curated notes per agent spec ("lessons", conventions, facts) | Across runs | Agents via a memory tool, with policy and provenance | Agents, loaded or searched at start | 4 |

Notes:

- M2 is the simplest useful memory: an agent that writes `notes.md` and later re-reads it has external memory without any special mechanism. Many "memory" benefits come from here.
- M4 can use Anthropic's client-side **memory tool** (`memory_20250818`: `view`, `create`, `str_replace`, `insert`, `delete`, `rename` on a `/memories` directory). The tool is client-executed, so the kernel implements the storage. In this lab that storage is the journal (every memory write is an event) plus a projection that materialises the current files. Verified from the API docs; our integration is Phase 4.

## 3. Long context within one instance

Long tasks fill M0. Options, all compatible with the append-only rule (AGENT-MODEL §3):

| Technique | What it does | Status |
|---|---|---|
| **Context editing** (server-side, beta `context-management-2025-06-27`, `clear_tool_uses_20250919`) | Clears old tool results from what the model sees; our transcript is unchanged | Verified in docs |
| **Server-side compaction** (beta `compact-2026-01-12`) | API summarises earlier context near a threshold; the compaction block must be kept in the transcript | Verified in docs |
| **Simple compaction** (client-side) | When too long: summarise, then start a **new instance** whose first message is the summary; replay nothing else | Recommended in Anthropic's migration guide; it is a fresh conversation, so no thinking blocks are invalidated |
| Keep-tail or background compaction | Summarise old turns, keep recent turns verbatim | **Breaks** preserved thinking on Opus 5.5 unless thinking blocks are stripped or `drop_block` is set. Avoid. |

Simple compaction is the same operation as delegation with a brief: a new context built from a summary. The kernel implements both with one mechanism, and E004 (context transfer) measures how much survives.

## 4. What is persisted

| Data | Where | Why |
|---|---|---|
| Every event | Journal (`events`) | Source of truth, replay, analysis |
| Full model requests and responses (including thinking blocks with signatures) | Blobs | Exact replay; inspecting what the model saw. Thinking text is empty under the default display; signatures are opaque. |
| Tool inputs and outputs | Events + blobs | Observability, replay |
| Workspace file versions | Blobs (by content hash), referenced by `workspace.file_written` events | Diffs, reversibility |
| Agent specs, policies, task fixtures | Git (TOML), referenced by hash in `run.started` | Reproducibility |
| Long-term memory (M4) | Events + materialised projection | Provenance for every memory item |
| API key | `.env` only | Never in the journal |

## 5. Memory risks

| Risk | Example | Mitigation |
|---|---|---|
| **Poisoning** | An injected instruction from a workspace file gets saved as a "lesson" and affects every future run | Memory writes carry provenance (run, task, source events, taint flag). Writes from tainted instances need approval or are quarantined. |
| **Staleness** | A memory says "tests are run with pytest -x" after the project changed | Memory items carry creation time and source; recall shows age; experiments measure harm from stale items. |
| **Experiment contamination** | Run 7 benefits from memory written by run 3, so runs are not independent | Memory store is versioned; `run.started` records the memory snapshot hash; experiments either disable memory or fix the snapshot. |
| **Context bloat** | Loading all memories at start drowns the task | Load an index (titles + one-line descriptions); read details on demand (the same pattern as skills). |
| **Privacy** | Memory accumulates personal data that is then sent to the API | Memory is per lab, inspectable, and deletable; no secrets rule applies. |

## 6. Memory experiments (summary)

Details in `EXPERIMENTS.md`:

- E005a: memory off vs workspace notes (M2) vs long-term memory (M4) on a repeated task family.
- E005b: agent-written memory vs human-curated memory.
- E005c: poisoning: plant a bad memory and measure how often it causes failures, and whether provenance display helps a reviewer agent catch it.
