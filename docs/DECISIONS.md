# Decision log

Format: ID · title · status · context · decision · consequences · revisit when. A decision changes only by adding a new entry that supersedes or amends it; old entries stay as written.

## Current status (2026-10-07, after Phase 1)

The owner approved Phase 0 on 2026-10-07. Entries D-001 to D-017 below were written as *Proposed*; their current status is:

| Decision | Status |
|---|---|
| D-001, D-002, D-004, D-007, D-009, D-010, D-011, D-012, D-014, D-015, D-016, D-017 | Accepted; implemented where Phase 1 needs them |
| D-003 | Accepted; amended by D-020 (synchronous loop in Phase 1) |
| D-005 | Accepted; refined by D-022 (capability names, no `request_capability` yet) |
| D-006 | Accepted; not implemented (no delegation in Phase 1) |
| D-008 | **Superseded by D-019** |
| D-013 | **Superseded by D-018** |
| ARCHITECTURE §5 stack table (Pydantic, rich) | Amended by D-021 |
| OBSERVABILITY envelope and replay | Refined by D-023 and D-024 |
| D-026, D-027, D-031 | Accepted 2026-10-07; implemented (core refactor, MODEL-GATEWAY steps 1-6) |
| D-028, D-029, D-030 | Accepted 2026-10-07 as the local runtime/model/tool-calling *candidates*; not implemented (step 7+) |
| D-003, D-007, D-025 | Amended by D-026, D-027, D-031 |
| D-032 | Accepted; records the compatibility breaks of the refactor |

---

---

### D-001 Build our own kernel on primitives; no agent framework at the core
- **Status:** Proposed (2026-10-07)
- **Context:** The lab's purpose is understanding agents. Frameworks (LangGraph, Pydantic AI, CrewAI, Agent SDK) hide the loop, context handling, and delegation we want to study, and impose their vocabulary.
- **Decision:** Implement the loop, syscall layer, policy engine, journal, and delegation ourselves on top of the `anthropic` SDK, Pydantic, and SQLite. Borrow ideas freely (LangGraph checkpoints/fork, Managed Agents versioning/events/permission policies).
- **Consequences:** More code to write and maintain; every part is visible and explainable. No framework lock-in.
- **Revisit when:** A component becomes large, generic, and uninteresting (e.g. eval harness → consider Inspect AI).

### D-002 Python 3.14 for the kernel; TypeScript only for the Observatory frontend
- **Status:** Proposed
- **Context:** Python has the strongest eval/analysis ecosystem (Inspect AI, Pandas/DuckDB, notebooks) and reads plainly. Browser UI is easiest in TypeScript.
- **Decision:** Kernel, tools, CLI, experiments in Python 3.14 managed by `uv`. Frontend (Phase 3) in TypeScript + React + Vite.
- **Consequences:** Two languages, separated by an HTTP/SSE boundary.
- **Revisit when:** The frontend needs logic that duplicates kernel logic.

### D-003 Manual agent loop over the Messages API
- **Status:** Proposed
- **Context:** Options were manual loop, SDK Tool Runner (beta), Managed Agents, Agent SDK (RESEARCH §2.3).
- **Decision:** Own the loop with `AsyncAnthropic.messages` (streaming where responses are long).
- **Consequences:** We must implement stop-reason handling (`tool_use`, `end_turn`, `max_tokens`, `refusal`, `pause_turn`), parallel tool results, and prefix discipline ourselves. That is the learning goal.
- **Revisit when:** Never for the kernel; Tool Runner or Managed Agents may appear as comparison baselines.

### D-004 Event-sourced journal in SQLite with content-addressed blobs
- **Status:** Proposed
- **Context:** Replay, fork, and the observability questions require full history. Single user, 5.8 GB RAM.
- **Decision:** Append-only `events` table (single writer, per-run `seq`), `blobs` table keyed by SHA-256, rebuildable projections. WAL mode, STRICT tables.
- **Consequences:** No Postgres/broker. Storage grows with full request/response blobs (compressed); runs can be pruned whole.
- **Revisit when:** Concurrent writers or dataset size outgrow SQLite (not expected).

### D-005 Complete mediation and capability-based permissions, default deny
- **Status:** Proposed
- **Context:** The model is untrusted; permission checks must have one place to live.
- **Decision:** All effects go through the syscall layer. Capabilities are scoped and attenuated on delegation. Policy decisions are allow/deny/ask, first-match rules, default deny, each recorded with the matched rule.
- **Consequences:** Tool handlers cannot use OS/network APIs directly (lint rule). Application-level checks are documented as not being isolation.
- **Revisit when:** MCP or other external tool sources are added (must be wrapped by the same layer).

### D-006 Hub-and-spoke communication; delegation as a kernel tool
- **Status:** Proposed
- **Context:** Direct agent chat obscures causality and invites "agents agreeing" designs. Evidence on multi-agent value is task-dependent (RESEARCH §3).
- **Decision:** Agents interact only via kernel calls: `delegate`, `complete_task`, workspace files, `ask_human`. Child agents start with a fresh context built from an explicit brief. Synchronous first.
- **Consequences:** Context transfer is measurable. Peer messaging and async delegation require new decisions.
- **Revisit when:** E004/E011 show a need.

### D-007 Append-only transcripts with a prefix-invariant check
- **Status:** Proposed
- **Context:** Opus 5.5 binds thinking blocks to the exact conversation prefix; edits are 400 errors on newer accounts and break prompt caching.
- **Decision:** Never edit an instance's transcript. The Model Gateway verifies that each request extends the previous one byte-for-byte. Use appended system messages, appended reminder blocks, server-side context management, or a new instance for compaction.
- **Consequences:** Some tricks (deleting old tool output client-side, rewriting the system prompt) are unavailable by design.
- **Revisit when:** API rules change.

### D-008 Explicit termination via `complete_task`
- **Status:** Proposed
- **Context:** `end_turn` does not mean success; forced tool choice is unavailable on Opus 5.5.
- **Decision:** Every agent finishes by calling `complete_task` (strict schema). One nudge if it forgets; then `no_completion` failure.
- **Consequences:** Clear task outcomes; "forgot to finish" becomes measurable.

### D-009 The kernel owns retries
- **Status:** Proposed
- **Context:** SDK retries are invisible to the journal.
- **Decision:** Client `max_retries=0`; the gateway retries transport errors with backoff and journals each attempt. Tool retries only for idempotent tools. Semantic retries are the model's job.
- **Consequences:** Slightly more gateway code; complete visibility of flakiness.

### D-010 Sandbox ladder: no exec in Phase 1; hardened Docker in WSL2 in Phase 2
- **Status:** Proposed
- **Context:** Windows Sandbox unavailable (Home). Docker Engine runs in WSL2 (observed). gVisor/microVM/srt unverified here.
- **Decision:** Phase 1: L0 + L1 only. Phase 2: `run_python` in containers with `--network none`, read-only root, dropped capabilities, non-root user, resource limits, workspace copied in (no Windows mounts). Higher rungs only via experiments S-02…S-05.
- **Consequences:** Phase 1 agents cannot run code. Phase 2 isolation is container-grade, explicitly not VM-grade.
- **Revisit when:** S-02/S-03/S-04 results.

### D-011 Own event schema; no external observability platform yet
- **Status:** Proposed
- **Context:** OTel GenAI conventions are in Development status; hosted tracing tools would duplicate the journal.
- **Decision:** The journal schema is the source of truth. OTel export is a later optional projection.
- **Consequences:** We build our own viewers (CLI, then Observatory).

### D-012 Cassette-based replay for tests
- **Status:** Proposed
- **Decision:** Recorded runs are golden fixtures; the full test suite runs offline with no API calls. Live smoke tests are opt-in and budget-capped.
- **Consequences:** Kernel determinism is a tested property.

### D-013 Model defaults and refusal fallback
- **Status:** Proposed
- **Context:** `claude-opus-5-5` is the current default Opus; its effort default is `medium`; safety classifiers can return `refusal`; server-side fallbacks can answer from another model.
- **Decision:** Default `claude-opus-5-5` with effort set explicitly per spec. Enable server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) so a false-positive refusal does not kill a run, and record the model that actually answered; experiments flag fallback runs. A spec can disable fallback for strict experiments.
- **Consequences:** A few runs may mix models; visible in the journal.
- **Revisit when:** E013 results or pricing changes.

### D-014 No vector database, embeddings, or RAG until an experiment needs it
- **Status:** Proposed

### D-015 CLI first; web Observatory in Phase 3 (SSE + REST, localhost only)
- **Status:** Proposed
- **Context:** The event model must be proven before building views on it.
- **Decision:** Phase 1–2 terminal tools (`lab trace`, `lab tree`, `lab show`). Phase 3 browser app reading projections and an SSE stream; commands via POST; bound to 127.0.0.1.

### D-016 Per-run disposable workspaces; agents never touch real project files
- **Status:** Proposed
- **Decision:** Each run copies a task fixture into `runs/workspaces/<run_id>/`; all file tools are confined there; results are exported deliberately by the human.

### D-017 Configuration in TOML, referenced by content hash
- **Status:** Proposed
- **Decision:** Agent specs, policies, tasks, experiment variants are TOML in Git (`tomllib`, no extra dependency). `run.started` records the hash of each.

---

## Phase 1 decisions (2026-10-07)

### D-018 Model fallback is off; requested and actual model are both recorded; a mismatch fails the run
- **Status:** Accepted, implemented. **Supersedes D-013.**
- **Context:** D-013 proposed enabling server-side refusal fallback by default. The owner rejected silent model switching: an experiment must mean what it claims.
- **Decision:** The kernel never sends the `fallbacks` parameter (fallback is opt-in on the API; without it a refused request simply stops). `run_started` records `model_requested` and `fallback: "disabled"`; every `model_response` records `requested_model` and `actual_model`. If they differ, the run fails with `model:model_mismatch`. A refusal ends the run as `stop_reason:refusal`. Opt-in fallback is **not implemented**; adding it later needs a new decision and must keep both fields.
- **Consequences:** False-positive refusals end runs instead of being rescued. Visible in the journal; acceptable for experiments.

### D-019 Phase 1 termination is `end_turn`; task success is decided by deterministic checks
- **Status:** Accepted, implemented. **Supersedes D-008.**
- **Context:** D-008 proposed a `complete_task` tool. With one agent, the owner's loop sketch (stop when the model requests no tool) is simpler, and success is better judged outside the agent anyway.
- **Decision:** The loop ends when the model stops with `end_turn` (status `completed`) or any other stop reason / limit (status `failed`). Whether the task *succeeded* is decided by the task's checks (`check_result` events), never by the agent's own claim.
- **Consequences:** "Agent finished" and "task succeeded" are separate fields. `complete_task` returns when delegation needs structured child results (Phase 2).

### D-020 Phase 1 loop is synchronous
- **Status:** Accepted, implemented. **Amends D-003 and ARCHITECTURE §7 ("asyncio from the start").**
- **Context:** One agent has nothing to run concurrently. A learner should be able to read the loop without `async`/`await`.
- **Decision:** Synchronous `anthropic.Anthropic` client and a plain `while True` loop (`src/agentlab/agent.py`). Tool calls in one turn run one after another.
- **Revisit when:** Phase 2 delegation needs concurrent child agents.

### D-021 Phase 1 runtime dependency is only the `anthropic` SDK
- **Status:** Accepted, implemented. **Amends the ARCHITECTURE §5 stack table** (Pydantic, rich).
- **Context:** Three tools with string arguments do not need a schema library; ASCII output reads fine in PowerShell.
- **Decision:** Tool schemas are hand-written JSON Schema (`strict: true`) with a ten-line validator; the trace is plain text. Pydantic is still installed (the SDK depends on it) but our code does not import it. `pytest` and `ruff` are dev-only.
- **Revisit when:** tools get structured or nested inputs.

### D-022 Capabilities are `filesystem.read` / `filesystem.write`, scoped by path globs in policy rules
- **Status:** Accepted, implemented. **Refines D-005.**
- **Decision:** Each tool needs one capability. A policy rule = capability + optional path globs (`out/**`) + decision (allow/deny/ask); first match wins; default deny. Paths are canonicalised by the workspace *before* the policy sees them; an invalid path fails as `tool_failed: invalid_path` without consulting the policy. Matching is case-insensitive (Windows).
- **Not implemented yet:** `request_capability` (agents asking for more rights), capability attenuation (needs delegation), the `<resource>:<action>:<scope>` naming from TOOL-MODEL.md.

### D-023 Event envelope for Phase 1
- **Status:** Accepted, implemented. **Refines D-004 and OBSERVABILITY §2.**
- **Decision:** Every event has `run_id`, `seq` (per run, assigned inside a write transaction), `event_id` (UUIDv7), `ts` (UTC), `type`, `agent_id`, `task_id`, `payload` (JSON). Event type names are snake_case (`tool_requested`, `permission_checked`, ...). The events of one tool call are linked by `tool_use_id` in their payloads; `causation_id`, `correlation_id` and span ids are not implemented. SQLite triggers reject UPDATE and DELETE. Large bodies are content-addressed blobs (SHA-256 of canonical JSON, zlib-compressed). `run_started` stores the full task definition and the starting files, so a run's record is self-contained.
- **Consequences:** Runs can be inspected and replayed even after the task files change (E010 R4).

### D-024 Replay means deterministic re-execution of the kernel against recorded inputs
- **Status:** Accepted, implemented; semantics established by E010. **Refines OBSERVABILITY §5.**
- **Decision:** Three separate operations: *inspection* (`trace`, `show`, `reconstruct_transcript`); *replay* (`agent-lab replay`): run the current kernel with recorded model responses, recorded human decisions and recorded starting files, require byte-identical requests and matching events, never call the model, never write to the journal; *rerun* (`agent-lab rerun`): a new live run with the recorded task and files. Tools are re-executed during replay (Phase 1 tools are deterministic). Transient retries are skipped; a run that ended in a permanent API error replays only up to the error. Fork is not implemented.

### D-025 Spend control is an estimate with a worst-case pre-check
- **Status:** Accepted, implemented.
- **Decision:** Before each model call the budget refuses if `spent + max_tokens_per_response x output price > max_cost_usd`, or if turns/tokens are exhausted. Cost is estimated from the usage the API reports and list prices in `budget.py`. A model without a known price cannot run live. Limits come from the task file (defaults: 10 turns, 30 tool calls, $0.50).
- **Consequences:** Not a bill; input tokens of the *next* call are not predicted. Good enough to make runaway runs hard.

---

## Free-first revision (2026-10-07) — PROPOSED, awaiting owner approval

### D-026 Free-first: no paid API is ever required
- **Status:** Proposed. **Changes the live path of Phase 1 (D-003).**
- **Context:** The owner will not spend money on API credits. Phase 1's only live path was the Anthropic API.
- **Decision:** Tests use the scripted backend (no network, key, or model). The default real model is local. External APIs are optional adapters, disabled unless the extra is installed, an explicit flag is set, the task names the backend, and a key exists. Experiments are costed in time and RAM.
- **Consequences:** E000/E001 live arms move to the local backend; Anthropic-only features (effort, preserved thinking) become adapter options.

### D-027 Backend-neutral model interface
- **Status:** Proposed. **Amends D-003 (the loop no longer speaks the Messages API) and D-007 (the prefix check moves to neutral messages).**
- **Decision:** `ModelRequest` / `ModelResponse` / `ToolCall` / `ToolResult` as specified in `MODEL-GATEWAY.md` §2. The kernel and loop never import a provider SDK. Provider state that must round-trip (Anthropic thinking signatures) travels in an opaque `provider_payload`. Core runtime dependencies become stdlib-only; `anthropic` moves to an optional extra.

### D-028 Primary local runtime: llama.cpp `llama-server`, native Windows, pinned build
- **Status:** Proposed.
- **Decision:** One OpenAI-compatible adapter (`OpenAICompatBackend`) talking to `llama-server` on `127.0.0.1`. CPU build first; Vulkan as an E020 arm. Ollama is the documented fallback (same adapter through its OpenAI-compatible endpoint). Not WSL, Docker, Transformers or LM Studio (`LOCAL-LLM.md` §2-3).
- **Revisit when:** E020 shows the setup or performance is unworkable.

### D-029 Default local model: Qwen3-1.7B, GGUF Q4_K_M, thinking off
- **Status:** Proposed.
- **Decision:** ~1.1 GB file pinned by SHA-256; 4k context; one server slot. Qwen3-4B Q4_K_M is the stretch arm; Qwen3-0.6B for plumbing smoke tests only.
- **Revisit when:** E020 (memory headroom) and E022 (task success) are in.

### D-030 Local tool calling: native template tool calls first, grammar-constrained JSON action as fallback
- **Status:** Proposed.
- **Decision:** Strategy A (`tools` + `--jinja`, server-parsed `tool_calls`) is the default. Strategy B (`response_format` JSON schema compiled to a grammar, one action per turn) is EXPERIMENTAL and compared in E024. Free-text parsing is rejected. Malformed arguments are journaled and answered with `invalid_input`; nothing unvalidated executes.

### D-031 Runaway guards for $0 backends; journal schema version 2
- **Status:** Proposed. **Amends D-025.**
- **Decision:** The spend cap applies only to priced backends. Every backend gets `max_wall_seconds` per run and `request_timeout_s` per model call, plus the existing turn, tool-call and token limits. `run_started.schema_version = 2`; Phase 1 (version 1) runs are not replayable after the refactor.

### D-032 Compatibility breaks of the provider-neutral refactor
- **Status:** Accepted, implemented (2026-10-07).
- **Decision / consequences:**
  - Journal `schema_version` 2. Phase 1 (v1) runs remain readable by `trace`, `show`, `runs`, but `replay` reports them NOT REPLAYABLE and `rerun` refuses them.
  - Event payloads: `tool_use_id` -> `tool_call_id`; `tool_requested` carries `arguments`, `raw_arguments`, `parse_error` (was `input`); `model_request` carries `backend`, `max_output_tokens`, `options`, `request_blob` (neutral) and `wire_blob` (backend body); `model_response` carries `finish_reason` (closed set) and `provider_finish_reason` instead of `stop_reason`, neutral `usage`, `tool_calls` instead of `tool_uses`.
  - Run failure reasons: `stop_reason:X` -> `finish_reason:X`; `model:api_error` -> `model:model_error`.
  - Task files: `[agent].model`/`effort` -> `[model] backend, name, temperature, seed, options`.
  - Scripts for the scripted backend are provider-neutral JSON; Anthropic-shaped scripts no longer load.
  - The `anthropic` package is no longer installed by `uv sync`; `uv sync --extra anthropic` adds it.
  - Free backends report `cost_usd: null`; the spend cap applies only to priced backends; new limits `max_wall_seconds` (default 900) and `request_timeout_s` (default 300).
