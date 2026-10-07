# Research (Phase 0)

Research done on 2026-10-07. Labels:

- **Observed**: checked directly on this machine during Phase 0.
- **Verified**: read in official documentation or a primary source (linked).
- **Reported**: from secondary sources; plausible but not checked against a primary source.
- **Not yet verified**: believed or hoped, must be tested before relying on it.

---

## 1. Environment reconnaissance (Observed)

| Item | Finding | Consequence |
|---|---|---|
| Project directory | Empty | Greenfield; no conventions to inherit except the parent lab |
| Parent lab `..\BAPE-LAB` | Git repo (no commits), Indonesian docs, experiment format with Observed/Inferred/Not-verified labels, `agents/README.md` requires threat model + least privilege + logged tool calls before any agent | Adopt its experiment format and safety rules |
| OS | Windows 11 **Home** 10.0.26200 | **Windows Sandbox is not available** on Home ([Microsoft docs](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-overview)) |
| CPU / RAM | Ryzen 7 5825U, 16 logical CPUs, **5.8 GB RAM** (the parent lab observed ~660 MB free in one snapshot) | Memory is the binding constraint. No heavy local services (no local LLMs, no Kubernetes, no big databases). |
| Disk | C: ~28 GB free | Fine for SQLite journals and small container images |
| Python | System 3.10.9; `uv` 0.12.21 manages 3.14.7 and 3.11.16 | Use `uv` with Python 3.14 (has `uuid.uuid7()`, checked) |
| Node | 24.11.1, npm 11.6 | Available for a later frontend and for Anthropic's `sandbox-runtime` if needed |
| SQLite (Python 3.14 bundled) | 3.53.1 | Supports JSON functions and STRICT tables |
| PostgreSQL | `psql` 18.6 on PATH; parent lab observed `postgres` listening on 0.0.0.0:5432 | Available but not needed; see storage decision |
| WSL | WSL 2.7.12, distro Ubuntu 24.04.4, kernel 6.18.33.2-microsoft-standard-WSL2; no `.wslconfig` | WSL VM saw **~2.8 GB** RAM (MemTotal 2,895,520 kB) |
| Docker | **Docker Engine 29.7.2 running inside WSL** (systemd `active`, cgroup v2). Linux user is in the `docker` group. No Docker Desktop on Windows. | Containers are available as a sandbox backend via `wsl.exe -- docker ...`. Membership in `docker` is root-equivalent inside that distro. |
| `/dev/kvm` in WSL | Present | Nested virtualisation is exposed; microVMs (Firecracker) or gVisor-KVM *might* work. **Not yet verified.** |
| bubblewrap / socat in WSL | Not installed | `sandbox-runtime` on Linux would need both |
| Anthropic credentials | `ANTHROPIC_API_KEY` unset; no `ant` profile in `~/.config/anthropic` | **An API key (Claude Console) is required before Phase 1 can call the model.** The Agent SDK docs state that claude.ai login may not be used for third-party agents. |
| Latest packages (PyPI/npm, 2026-10-07) | `anthropic` 1.11.0, `mcp` 2.3.0, `pydantic` 2.13.5, `inspect-ai` 0.3.277, `@anthropic-ai/sdk` 0.131.0, `@anthropic-ai/sandbox-runtime` 0.0.78, `@modelcontextprotocol/sdk` 1.32.1 | Pin versions at Phase 1 start |

---

## 2. The model API: facts that shape the architecture (Verified)

Source: Anthropic's Claude API documentation as bundled in the `claude-api` skill (cached 2026-09-25) and platform docs.

### 2.1 Tool calling on the wire

- Everything goes through one endpoint, `POST /v1/messages`. Tools and structured output are request features, not separate APIs.
- A tool is declared as `name`, `description`, and a JSON Schema `input_schema`. `strict: true` guarantees the model's arguments validate against the schema.
- The model asks for a tool by returning a `tool_use` content block (`id`, `name`, `input`) and `stop_reason: "tool_use"`. **The model executes nothing.** The caller runs the tool and sends a `tool_result` block (`tool_use_id`, `content`, optional `is_error: true`) in the next `user` message.
- One assistant message may contain several `tool_use` blocks (parallel calls). All results go back in **one** user message.
- Failed tools should return `is_error: true` with a useful message, not be dropped; the model usually adapts.
- Anthropic-defined client tools (bash `bash_20250124`, text editor `text_editor_20250728`, memory `memory_20250818`) have built-in schemas but are still executed by the caller. Server tools (web search, web fetch, code execution) run on Anthropic's infrastructure.

### 2.2 Constraints specific to Claude Opus 5.5 (the default model)

- Model ID `claude-opus-5-5`; 1M context; 128K max output; $4 / $20 per million input/output tokens; cache reads $0.20/MTok.
- **Thinking is always on.** `{type: "disabled"}` returns 400. Effort (`output_config.effort`: low/medium/high/xhigh/max) is the control. **Default effort is `medium`**, so it must be set explicitly for experiments.
- **Forced tool choice is rejected** (`tool_choice` `any`/`tool` → 400). Only `auto`/`none`. A harness that needs a tool call must ask for it in the prompt and check that it happened.
- **The raw chain of thought is never returned.** `thinking.display` controls what comes back: `"omitted"` (default, empty text), `"summarized"` (a summary), or `"updates"` (beta `thinking-display-updates-2026-08-18`: short progress notes between tool calls, reasoning hidden). Prompts that push the model to reproduce its internal reasoning as text may be refused with `stop_reason: "refusal"` and category `reasoning_extraction`.
- **Preserved thinking / append-only transcripts.** A thinking block is bound to the model that produced it and to the exact conversation prefix (system prompt, tool set, all earlier messages). Editing, reordering, or deleting earlier turns, rebuilding the system prompt, or changing the tool array between requests invalidates every later thinking block. For accounts created on or after 2026-08-31 this is a **400 error by default**; older accounts can opt in. Append-only alternatives exist: mid-conversation `role: "system"` messages, `tool_addition`/`tool_removal` (beta), server-side compaction and context editing.
- `stop_reason: "refusal"` (safety classifiers; HTTP 200) must be checked before reading content. Server-side `fallbacks` can retry on another model; the fallback model cannot read Opus 5.5's thinking blocks.

**Consequence for the lab (Inferred):** the API itself pushes toward an event-sourced, append-only design. A harness that treats each agent's transcript as an immutable log gets correct thinking preservation, maximal prompt-cache hits, and exact replay at the same time. This is the strongest single reason for the journal-centred architecture in `ARCHITECTURE.md`.

### 2.3 Loop helpers and hosted options (Verified)

Four ways to build an agent on Claude, distinguished by who supplies the loop ("harness") and who hosts it:

| Approach | Loop | Hosting | Tools | Fit for this lab |
|---|---|---|---|---|
| Manual loop over `messages.create` | You | You | Only yours | **Chosen.** Full control and full visibility of every step. |
| SDK Tool Runner (`client.beta.messages.tool_runner`) | SDK | You | Only yours | Good, with per-turn hooks for approval and interception. But it is beta and hides the loop, which is the thing we want to study. |
| Managed Agents (`/v1/agents`, `/v1/sessions`) | Anthropic | Anthropic container (or self-hosted sandbox) | Built-in toolset, MCP, yours | Excellent reference design (versioned agents, sessions, threads, event stream, `always_allow`/`always_ask`/`auto` permission policies). Hosted, so the loop is not ours to instrument. |
| Claude Agent SDK (`claude-agent-sdk`) | Claude Code harness | You | Claude Code's built-ins + MCP + subagents + hooks | Batteries included. Runs the Claude Code binary. Studying it teaches Claude Code, not agent fundamentals. |

Managed Agents' event taxonomy (`agent.tool_use`, `agent.tool_result`, `session.status_idle` with `stop_reason: requires_action`, `session.thread_created`, `span.model_request_start/end`, `user.tool_confirmation`, ...) and its multi-agent limits (one level of delegation, threads share a filesystem, subagents see none of the coordinator's conversation) are useful prior art for our own event model.

---

## 3. Multi-agent evidence

| Source | Claim | Status |
|---|---|---|
| Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | A lead Claude Opus 4 with Sonnet 4 subagents "outperformed single-agent Claude Opus 4 by 90.2%" on their internal research eval. Agents use ~4× the tokens of chat, multi-agent ~15×. Token usage alone explained ~80% of performance variance on BrowseComp. Good fit: heavy parallelism, information exceeding one context window, many complex tools. Poor fit: "most coding tasks involve fewer truly parallelizable tasks than research". Delegation briefs need an objective, output format, tool/source guidance, and task boundaries. Evaluate end states with an LLM judge; start with ~20 real queries. | Verified (primary source) |
| Cognition, [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) (2025-06-12) | Principles: "Share context, and share full agent traces, not just individual messages" and "Actions carry implicit decisions, and conflicting decisions carry bad results". Parallel subagents building parts of a Flappy Bird clone made incompatible assumptions. Recommends a single-threaded agent plus a compression model for long tasks. | Verified (primary source) |
| Cemri et al., [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) (NeurIPS 2025) | MAST taxonomy: 14 failure modes in 3 categories (specification issues, inter-agent misalignment, task verification), from 1600+ annotated traces across 7 frameworks. Failures mostly come from system design, not only model limits. | Verified (abstract and search summary; full paper not read) |

**Synthesis (Inferred):** the two industry positions are not contradictory. Isolation helps when sub-tasks are read-heavy and independent (research); it hurts when sub-agents make decisions that must be mutually consistent (writing into shared state). Token spend is a large confounder: a multi-agent system that wins may simply be spending more. Our experiments must therefore compare designs **at matched token budgets**, and must measure context loss at each handoff. Both are first-class design goals below.

---

## 4. Frameworks surveyed

| Option | What it offers | Why not the core here |
|---|---|---|
| LangGraph | Graph of nodes/edges; checkpointer saves state at each super-step; threads; [time travel: replay and fork from checkpoints](https://docs.langchain.com/oss/python/langgraph/use-time-travel) (nodes after the checkpoint re-execute, including LLM calls). | Strong ideas, which we borrow (checkpoint, fork). As a dependency it puts a graph abstraction between us and the loop, and its replay re-calls the model, which is not deterministic replay. |
| OpenAI Agents SDK, Google ADK | Provider-native agent loops, handoffs, tracing | Optimised for other model families. *Reported.* |
| Pydantic AI | Type-safe Python agents, multi-provider | Good typing story; we use Pydantic directly instead. *Reported.* |
| CrewAI, AutoGen | Role-based crews / conversational multi-agent | Tend toward the "agents chatting" pattern this lab wants to scrutinise, not assume. *Reported.* |
| Claude Agent SDK | See 2.3 | See 2.3 |
| Managed Agents | See 2.3 | Reference design; candidate comparison baseline in a late experiment |
| Inspect AI (UK AISI) | Eval framework: tasks, solvers, scorers, Docker sandboxes, transcript logs with a step-by-step viewer; companion Inspect Scout for transcript analysis | **Strong candidate for the evaluation layer later** (Phase 4). Not for the kernel. Integration details *Not yet verified*. |

Primitives we will use directly: the `anthropic` SDK (HTTP client, types, streaming), Pydantic (schemas, validation, JSON Schema generation), SQLite (journal), Docker (sandbox), and later the MCP SDK (tool source adapter).

---

## 5. Protocols and standards

### MCP (Verified: [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog))

The current MCP specification, 2026-07-28, is a major revision:

- Protocol-level sessions and the `initialize` handshake are **removed**; MCP is stateless. Every request carries its protocol version and client capabilities in `_meta`. Servers must implement `server/discover`.
- Server-initiated requests (sampling, elicitation, roots) are replaced by a Multi Round-Trip Request pattern (`resultType: "input_required"`).
- Tasks moved to an extension (`io.modelcontextprotocol/tasks`). Roots, Sampling, and Logging are deprecated. Trace context (`traceparent`) can travel in `_meta`.

**Consequence:** MCP is a good *adapter* for importing third-party tools into the kernel later (Phase 5), but the kernel's own tool model must not depend on it. Stateless MCP also fits the kernel model well: the kernel stays the only place where state lives.

### OpenTelemetry GenAI semantic conventions (Verified: status; Reported: details)

The GenAI conventions (spans like `invoke_agent`, `execute_tool`, `create_agent`, attributes like `gen_ai.agent.name`, `gen_ai.tool.call.id`) now live in a separate repository, [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai), and are still in **Development** status with no stabilisation timeline ([Greptime, May 2026](https://www.greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)). **Consequence:** our own event schema is the source of truth; an OTel exporter is an optional later projection.

---

## 6. Sandboxing options (detail in `SANDBOX.md`)

| Option | Status on this machine |
|---|---|
| Windows Sandbox | Unavailable (Home edition). Verified via Microsoft docs. |
| Hyper-V VMs | Hyper-V management tools are not part of Home. *Not yet verified on this machine.* |
| Docker Engine in WSL2 | **Observed running.** Namespaces + cgroups + seccomp; shares the WSL2 Linux kernel. |
| gVisor (`runsc`) in WSL2 Docker | *Not yet verified.* No source found confirming WSL2 support. |
| Firecracker / microVM in WSL2 | `/dev/kvm` present; *Not yet verified*. |
| Anthropic `sandbox-runtime` (`srt`) | [Verified README](https://github.com/anthropic-experimental/sandbox-runtime): OS-level filesystem and network restriction without containers; macOS (Seatbelt) and Linux (bubblewrap + seccomp) supported, **Windows alpha** (dedicated `srt-sandbox` user + Windows Filtering Platform egress rules). Read is allow-by-default with deny lists; write is deny-by-default with allow lists; network is deny-by-default via an allowlisting proxy. Documented caveats: domain fronting may bypass filtering, traffic is not inspected, proxy env vars can be ignored by programs, DNS resolution is not fenced. Status: beta research preview. |
| Anthropic server-side code execution tool | Verified: isolated container, 1 CPU / 5 GiB RAM / 5 GiB disk, no internet. Strong isolation (not on my machine) but outside our observability and control. |

### Prompt injection

Simon Willison's ["lethal trifecta"](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): an agent that has (1) access to private data, (2) exposure to untrusted content, and (3) a way to communicate externally can be steered by injected instructions to exfiltrate the data. Model-level defences are not reliable; the robust defence is to never give one agent all three. This becomes a design rule in `TOOL-MODEL.md` and an experiment in `EXPERIMENTS.md`.

---

## 7. What remains unverified (to be settled by experiments)

1. Whether gVisor `runsc` or a microVM runs under this WSL2 kernel (`SANDBOX.md`, S-03/S-04).
2. Whether a dedicated WSL distro with interop and automount disabled (`/etc/wsl.conf`) behaves as expected as a sandbox host (S-02).
3. Whether the `sandbox-runtime` Windows alpha or its Linux mode in WSL2 is usable here.
4. Exact API behaviour of beta features we plan to use (`thinking-display-updates-2026-08-18`, `task-budgets-2026-03-13`, `server-side-fallback-2026-07-01`) on this account. Phase 1 starts with a smoke test.
5. Whether this account is enforced on the preserved-thinking prefix check (depends on account creation date). The design is append-only either way.
6. How Inspect AI could drive our kernel as a solver (Phase 4).
7. Real cost per run. Arithmetic from list prices (Opus 5.5, no caching): 200K input + 20K output tokens = 0.2 × $4 + 0.02 × $20 = **$1.20**. Multi-agent runs may use several times more. Budgets are enforced by the kernel; see `AGENT-MODEL.md`.
