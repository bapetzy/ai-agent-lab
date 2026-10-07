# Architecture

> **Implementation status (Phase 1, 2026-10-07).** IMPLEMENTED: journal (SQLite events + blobs, append-only triggers), model gateway (prefix check, kernel-owned retries, requested-vs-actual model check), syscall path for three file tools (validate → canonical path → policy → optional human approval → execute → journal), agent loop with limits, per-run disposable workspaces, CLI (`run`, `rerun`, `runs`, `trace`, `show`, `replay`), offline scripted model. PROPOSED (not built): scheduler for many instances, delegation, sandbox backend, MCP adapter, API server and Observatory, projections tables, OTel export. Changes from this document: synchronous loop (D-020), only `anthropic` as runtime dependency (D-021), fallback disabled (D-018), `end_turn` termination (D-019). The diagram below is the target; only the parts listed as implemented exist.

Status: **proposed** (Phase 0). Decisions referenced as D-NNN live in `DECISIONS.md`.

## 1. The central idea: an agent kernel

An operating system kernel solves a problem that looks a lot like ours. Programs are untrusted. They run with their own private state. They cannot touch hardware or other programs directly; they make **system calls**, and the kernel decides, per call, whether the caller holds the right capability. The kernel schedules programs, enforces resource limits, isolates them, and can keep an audit log.

Map that onto agents:

| OS concept | Lab concept |
|---|---|
| Process | **Agent instance**: one running agent with its own context window and transcript |
| Program binary | **Agent spec**: versioned config (role, model, prompt, tools, limits) |
| System call | **Tool call**: the only way an agent affects anything |
| Capabilities / file permissions | **Capabilities** granted to an agent instance, attenuated on delegation |
| `fork` + `exec` / `wait` | **Delegate**: create a task and a child agent instance; wait for its result |
| Scheduler + rlimits | Scheduler with turn, token, time, and fan-out budgets |
| Process isolation | Sandbox for code-executing tools (containers in WSL2) |
| Audit log / journal | **Journal**: append-only event log; the system's source of truth |
| `/proc` | Projections and the Observatory |

The kernel is a **reference monitor**: every effect goes through it (complete mediation), it cannot be bypassed by the agent, and it is small enough to review. This framing is not decoration. It tells us where the security boundary is (the kernel and its executors, not the prompt), where observability comes from (the syscall boundary), and how delegation should work (capability attenuation, like a process that can only pass on rights it holds).

The second idea is **event sourcing with a determinism boundary**. Everything non-deterministic enters the system through the journal: model responses, tool results, human decisions, clock readings, generated IDs. Kernel logic between those inputs is deterministic. Therefore a run can be re-executed from its journal and must produce byte-identical model requests; any difference is a bug or a deliberate fork. Durable-execution engines such as Temporal use the same principle for workflows. The Claude API's append-only transcript requirement (RESEARCH §2.2) makes this a natural fit rather than an imposed one.

## 2. Diagram

```
┌──────────────────────────── Agent Observatory (Phase 3, browser) ────────────────────────────┐
│  run list · swimlane timeline per agent · task tree · tool-call inspector · run diff ·       │
│  approval queue · replay/fork controls                                                       │
└──────────────────────▲──────────────────────────────────────────────┬────────────────────────┘
                       │ SSE: live event stream                       │ REST: start run, approve/deny,
                       │                                              │ fork, query projections
┌──────────────────────┴──────────────────────────────────────────────▼────────────────────────┐
│ KERNEL — one Python process (asyncio). Trusted computing base.                               │
│                                                                                              │
│  ┌───────────── Scheduler ─────────────┐   ┌──────── Model Gateway ─────────┐                │
│  │ runs AgentInstances as asyncio tasks│   │ builds requests from transcript│──► Anthropic   │
│  │ budgets: turns, tokens, time,       │──►│ prefix-invariant check         │    Messages API│
│  │ tool calls, depth, fan-out          │   │ retries (owned by kernel)      │                │
│  └──────────────┬──────────────────────┘   │ records request/response blobs │                │
│                 │                          │ live │ replay(cassette) │ fork │                │
│  ┌──────────────▼──────────────┐           └────────────────────────────────┘                │
│  │ Agent Runtime (the loop)    │                                                             │
│  │ append-only transcript      │ tool_use blocks                                             │
│  └──────────────┬──────────────┘                                                             │
│                 ▼                                                                            │
│  ┌──────────────────────────── Syscall layer ───────────────────────────────┐                │
│  │ 1 parse + validate input (Pydantic, strict schema)                       │                │
│  │ 2 derive required capabilities from the *resolved* input                 │                │
│  │ 3 Policy Engine: allow / deny / ask  ──ask──► Approval Gate ──► human     │                │
│  │ 4 journal intent (write-ahead)                                           │                │
│  │ 5 execute ─┬─ kernel tools: delegate, complete_task, request_capability  │                │
│  │            ├─ workspace tools (in-process, path-confined)                │                │
│  │            ├─ sandbox tools ──► SandboxBackend ──► wsl.exe docker run ... │                │
│  │            └─ MCP adapter (Phase 5)                                      │                │
│  │ 6 journal outcome; truncate/label output; return tool_result             │                │
│  └──────────────────────────────────────────────────────────────────────────┘                │
│                 │ every step                                                                 │
│  ┌──────────────▼───────────────────────────────────────────────┐                            │
│  │ Journal: append(Event) → SQLite events table (+ blobs by hash)│──► projections: runs, tasks,│
│  │ single writer, monotonic seq per run                          │    instances, usage, files  │
│  └──────────────────────────────────────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
   Host: Windows 11 (kernel, journal, workspaces)      WSL2 Ubuntu: Docker sandbox containers
```

## 3. Components

| Component | Responsibility | Key rule |
|---|---|---|
| **Journal** | Append events; assign `seq`; store large payloads as content-addressed blobs; serve reads and live subscriptions | Single writer. Events are never updated or deleted. |
| **Projections** | Fold events into queryable views: run summary, task tree, instance status, usage, workspace file history | Always rebuildable from the journal. Never a source of truth. |
| **Scheduler** | Start/stop agent instances; enforce budgets and limits; cancellation | A limit hit is an event and a typed failure, never a silent stop. |
| **Agent Runtime** | The loop: request → response → tool calls → results → repeat until `complete_task`, failure, or limit | Transcript is append-only (D-007). |
| **Model Gateway** | Build requests; enforce the prefix invariant; call the API; retries; record blobs; serve recorded responses in replay mode | The only code that talks to the model. |
| **Syscall layer** | Validate, authorise, approve, execute, record every tool call | No tool runs without passing all steps. |
| **Policy Engine** | Evaluate capability rules → allow / deny / ask, with the matched rule recorded | Default deny. |
| **Approval Gate** | Present pending asks to a human (CLI prompt in Phase 1, Observatory queue later); record the decision | Agents wait; they never decide for the human. |
| **Tool Registry** | Tool specs and handlers | Tools receive scoped handles, never raw `os`/network access. |
| **Sandbox Backend** | Run code in an isolated container with resource and network limits | Kernel holds the Docker access; agents never do. |
| **CLI** (Phase 1) / **API server + Observatory** (Phase 3) | Start runs, inspect, approve, replay, fork | Read paths come from projections; write paths go through the kernel. |

Detailed models: agents and tasks in `AGENT-MODEL.md`, tools and permissions in `TOOL-MODEL.md`, events and replay in `OBSERVABILITY.md`, isolation in `SANDBOX.md`, memory in `MEMORY.md`.

## 4. Data flow of one tool call

1. The model returns an assistant message with `tool_use` blocks. The gateway journals `model.response` (blob = full response JSON) and the runtime appends the message to the transcript unchanged, thinking blocks included.
2. For each `tool_use`, the syscall layer journals `tool.call_requested`, validates the input, and resolves it (for example canonicalises a path).
3. The policy engine computes the required capabilities from the resolved input and journals `permission.decided` with the outcome and the matching rule.
4. On `ask`, `approval.requested` is journaled and the instance status becomes `waiting_approval` until `approval.resolved`.
5. On allow, the call executes; `tool.started` and then `tool.completed` or `tool.failed` are journaled (output blob, duration, exit code, truncation flag).
6. On deny or failure, the model still gets a `tool_result` with `is_error: true` and a structured explanation. It is never silently dropped.
7. All results of that turn go back in one user message; the runtime loops.

## 5. Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Language (kernel) | **Python 3.14** via `uv` | Readable for learning; first-class `anthropic` SDK, Pydantic, Inspect AI, MCP SDK; `asyncio` for concurrent agents; stdlib `sqlite3`, `tomllib`, `uuid.uuid7`. |
| Model access | **`anthropic` SDK** (1.x, `AsyncAnthropic`), **manual loop** | We want to own and see the loop (D-003). SDK still gives types, streaming, errors. |
| Default model | `claude-opus-5-5`, effort set explicitly; model and effort are experiment variables | Most capable default with lower price than Opus 5; cheaper models (Sonnet 5.5, Haiku 4.5) for worker roles where experiments justify them. |
| Schemas | **Pydantic v2** | One definition gives validation, JSON Schema for `input_schema`, and typed events. |
| Storage | **SQLite** (single file per lab, WAL mode, STRICT tables) | Zero-ops, ships with Python, easy to copy or diff a run, enough for one user. |
| Config | **TOML** files for agent specs, tasks, policies (`tomllib`) | Readable, comments allowed, no extra dependency. |
| CLI | `argparse` + **`rich`** | `rich` earns its place by making trace timelines readable in the terminal. |
| Sandbox | **Docker Engine in WSL2**, invoked as `wsl.exe -d <distro> -- docker run ...` | Already installed and running; real namespace/cgroup isolation. Phase 2. |
| Tests | `pytest`, cassette-based replay; `ruff` for lint/format | Offline, deterministic, free. |
| Observatory (Phase 3) | Python API (Starlette or FastAPI) with **SSE** + REST; frontend **TypeScript + React + Vite** | Event stream is one-way, so SSE is simpler than WebSockets; commands are plain POSTs. Rich visualisation is easiest in the browser. |
| Eval (Phase 4) | Our own experiment runner first; evaluate Inspect AI as the harness | Avoid adopting a framework before we know what we need from it. |

Phase 1 runtime dependencies: `anthropic`, `pydantic`, `rich`. Dev: `pytest`, `ruff`.

## 6. Alternatives considered

| Stack | Strengths | Why it loses here |
|---|---|---|
| **A. Framework-first** (LangGraph / Pydantic AI / CrewAI) | Fast start, checkpointing, ecosystem | Hides the loop and the context handling we want to study; framework concepts become the vocabulary instead of agent concepts; replay re-calls the model. |
| **B. Claude Agent SDK** | Claude Code's whole harness: tools, hooks, permissions, subagents, sessions | Teaches Claude Code, not agents. Built-in tools blur the security boundary. Runs a bundled binary. |
| **C. Managed Agents** | Hosted loop, sandboxes, threads, permission policies, rich events | Not local; loop not ours; costs container runtime. Kept as a reference design and a possible late baseline. |
| **D. TypeScript end-to-end** | One language including frontend; `sandbox-runtime` library is TS | Weaker eval/analysis ecosystem (Inspect, notebooks, Pandas/DuckDB); Python reads more plainly for learning. Frontend stays TS anyway. |
| **E. Distributed services** (message broker, Postgres, separate agent workers) | Scales; "real" architecture | Nothing to scale. 5.8 GB RAM. More moving parts than questions they answer. The journal already is a message log; a broker can be an experiment later. |
| **F. Run everything inside WSL2** | Linux-native sandboxing (bubblewrap, Docker socket local) | Workspaces and tooling live on Windows; editor and parent lab are on Windows. Revisit if the `wsl.exe` boundary proves painful (it is behind one interface). |

**Why the recommendation wins:** it maximises learning value (every concept is a visible piece of code), observability (one choke point for all effects), replayability (determinism boundary), and safety (default deny + sandbox), with three runtime dependencies and no services. Its cost is that we write the loop, the policy engine, and the journal ourselves; that cost is the point of the lab.

## 7. Backend strategy

- Phase 1 is a **library plus CLI** in one process: `lab run`, `lab trace`, `lab replay`. No server.
- `asyncio` from the start, because delegation (Phase 2) runs child agents concurrently and retrofitting async is a rewrite.
- The kernel exposes an internal API (`start_run`, `resolve_approval`, `fork_run`, `subscribe(events)`); the CLI uses it now, and the HTTP server wraps the same API in Phase 3.
- Retries are owned by the kernel, not hidden in the SDK (`max_retries=0` on the client), so every retry is an event (D-009).

## 8. Frontend strategy

- Phase 1–2: terminal only. `lab trace <run>` prints a timeline; `lab show <event>` prints one event with its blobs; `lab tree <run>` prints the task/delegation tree.
- Phase 3: the **Agent Observatory**. Planned views: swimlane timeline (one lane per agent instance, tool calls as spans, delegations as arrows), task tree, tool-call inspector (input, policy decision, approval, output, duration), context viewer (exact request a model saw, with the delta from the previous request), run diff (two runs or a run and its fork, aligned by step), approval queue, replay/fork controls.
- The Observatory is read-mostly and stateless: all state comes from projections and the SSE stream.

## 9. Storage strategy

- One SQLite database (`runs/lab.db`, git-ignored) with:
  - `events` (append-only; envelope columns + JSON payload),
  - `blobs` (SHA-256 → bytes, compressed; raw API requests and responses, large tool outputs, file snapshots),
  - projection tables (rebuildable; may be dropped at any time).
- Per-run workspaces under `runs/workspaces/<run_id>/`, created from a task fixture. Agents never see paths outside their workspace.
- Agent specs, task bank, and policies are TOML files in Git; a run records the content hash of every spec and policy it used.
- `lab export <run>` writes a run as JSONL + blobs for sharing or fixtures; cassettes for tests use the same format.
- **Not chosen:** Postgres (ops cost, no need), a vector database (no retrieval need until memory experiments show one), a broker (journal suffices).

## 10. Testing strategy

| Level | What | How |
|---|---|---|
| Unit | Policy rules, capability matching, attenuation, budgets, event schemas | Table-driven pytest |
| Path confinement | Traversal (`..`), absolute and drive paths, UNC paths, symlinks and junctions, Windows case-insensitivity, 8.3 short names, alternate data streams (`file:stream`), reserved names (`CON`, `NUL`), trailing dots/spaces | Adversarial table of inputs; each must be rejected or resolved inside the workspace |
| Kernel | Loop behaviour with a **scripted fake model** (returns predefined tool_use sequences) | No API calls |
| Replay | Recorded real runs (cassettes) re-executed; requests must match byte-for-byte | Golden tests; any kernel change that alters requests fails loudly |
| Sandbox | Container flags actually applied; network blocked; resource limits enforced; workspace-only mount | Integration tests, opt-in marker (needs WSL) |
| Live smoke | One tiny real run per beta feature we rely on | Opt-in marker, hard budget cap |
| Evaluation | Agent quality | Not tests. Experiments (`EXPERIMENTS.md`). |

## 11. Development workflow

- `uv` project; `uv run pytest`, `uv run ruff check`.
- Git repository initialised at Phase 1 start (with owner approval); conventional commits as in the parent lab (`docs:`, `feat:`, `experiment:`).
- `.env` (git-ignored) holds `ANTHROPIC_API_KEY`; the kernel loads it; nothing else may read it.
- Every architectural change starts as an entry in `DECISIONS.md`.
- Every experiment gets a folder `experiments/NNN-name/` with the hypothesis written before the run.
- Cassettes from real runs become regression fixtures; live API calls are never needed to run the test suite.

## 12. Security assumptions

1. **Single trusted operator** (me) on a single machine. No remote users, no network-exposed services. The Phase 3 server binds to `127.0.0.1` only.
2. **The kernel and its executors are trusted** (the trusted computing base). Their bugs are security bugs.
3. **The model and everything it reads are untrusted.** That includes workspace files, tool outputs, web content, and other agents' outputs. Any of them may contain injected instructions.
4. **Anthropic's API is a trusted third party that sees all context.** Anything placed in a workspace or tool output is sent to the API. Workspaces must not contain secrets or personal data I would not send.
5. **Application-level permission checks are not isolation.** They stop well-behaved and mistaken agents; they stop malicious behaviour only as far as the tool code is correct. Code execution therefore runs only inside the sandbox (SANDBOX.md), which is defence in depth, not a perfect boundary.
6. **Docker access is root-equivalent inside the WSL distro.** Only the kernel uses it. No agent tool may mount the Docker socket or invoke Docker.
7. Secrets (API key) never enter workspaces, sandboxes, events, or tool outputs. The journal records request bodies, so the key must stay in headers (the SDK puts it there) and never in prompts.

## 13. Known limitations

- Isolation is weaker than a VM: containers share the WSL2 kernel, and the WSL distro has Windows interop and drive mounts unless disabled (SANDBOX.md).
- Model behaviour is non-deterministic. Exact reproduction is only possible from recordings; re-running live gives a new sample. Experiments need repetitions.
- The chain of thought is not observable. We see actions, tool inputs/outputs, and optional model-written progress notes or summaries, which are themselves model output and may not be faithful.
- Several features we plan to use are beta and may change (progress-update display, task budgets, server-side fallbacks, mid-conversation tool changes).
- 5.8 GB RAM limits concurrent containers and rules out local models.
- Cost scales with tokens; multi-agent designs can cost an order of magnitude more than single agents (RESEARCH §3).
- SQLite with a single writer is a deliberate ceiling; fine for one user and tens of concurrent agents.

## 14. What NOT to build yet

- No web UI before the journal and CLI trace prove the event model (Phase 3).
- No peer-to-peer agent chat, no message broker, no blackboard service (later experiments, if delegation shows a need).
- No vector database, embeddings, or RAG.
- No long-term memory until the memory experiment phase.
- No MCP until the native tool model is stable.
- No OpenTelemetry, Langfuse, or LangSmith integration.
- No general shell tool for agents; code execution only via a sandboxed `run_python`-style tool in Phase 2.
- No internet access for agents; web tools only in a dedicated, later experiment with the lethal-trifecta rule enforced.
- No plugin system, no multi-user auth, no deployment.
