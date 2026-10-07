# Agent model

> **Implementation status (Phase 1).** IMPLEMENTED: one agent per run with `run_id`, `agent_id` (instance) and `task_id` on every event; agent configuration in the task file (`[agent]`: model, effort, system prompt, tools; `[limits]`); append-only transcript enforced by the gateway's prefix check; limits on turns, tool calls, input/output tokens, estimated cost, tokens per response; typed failure reasons (`limit: ...`, `stop_reason:...`, `model:api_error|model_mismatch|prefix_violation`, `kernel_error:...`); kernel-owned transport retries. SUPERSEDED: §4 `complete_task` (replaced by `end_turn` + deterministic checks, D-019). PROPOSED: versioned AgentSpec files, task tree, delegation, budgets carved for children, repetition/cycle detection, crash recovery.

This document answers: what an agent is, what an agent object should contain, how tasks are represented, how agents delegate and communicate, how loops are prevented, and how failures and retries work.

## 1. What an agent actually is

A language model is a stateless function: request in, response out. It remembers nothing between calls and cannot act. Everything we call "an agent" is built around it by the caller:

```
agent = model + loop + context + tools + policy + termination rule
```

- **Loop.** Send the transcript; if the response asks for tools, run them, append the results, send again. Stop when a termination rule fires.
- **Context.** The transcript: system prompt, tool definitions, and every message so far. It is the agent's only working memory. The model sees exactly what is in the request and nothing else.
- **Tools.** Structured requests the model can emit. The model only *proposes* a call; the harness decides whether to run it.
- **Policy.** What the agent is allowed to do: capabilities, budgets, approvals.
- **Termination rule.** When the agent is done, failed, or out of budget.

Phase 1 begins with an experiment (E000, `EXPERIMENTS.md`) that builds this loop by hand in about sixty lines, with no kernel, so that every later abstraction can be compared to the bare mechanism.

## 2. AgentSpec: the definition (immutable, versioned)

An `AgentSpec` is configuration, stored as TOML in Git and identified by name + content hash. Changing anything produces a new version; runs record the exact hash they used. (Managed Agents uses the same split between a versioned agent and a session that pins a version; it is the property that makes A/B experiments possible.)

| Field | Meaning |
|---|---|
| `name`, `version`, `hash` | Identity. `hash` = SHA-256 of the canonical spec. |
| `role` | One-paragraph description of what this agent is good at and what to hand it. Other agents read this when choosing whom to delegate to. |
| `model`, `effort`, `thinking_display` | e.g. `claude-opus-5-5`, `medium`, `updates`. Set explicitly; Opus 5.5 defaults to `medium` effort. |
| `system_prompt` | Template + rendered text hash. Frozen for the life of an instance. |
| `tools` | Names of tools offered to the model. Declared up front: the tool array cannot change mid-conversation without breaking the transcript prefix. |
| `grants` | Capabilities this spec may hold (upper bound; see `TOOL-MODEL.md`). |
| `can_delegate_to` | Names of specs it may delegate to (its roster). Empty = cannot delegate. |
| `limits` | `max_turns`, `max_tool_calls`, `max_tokens_total`, `max_wall_seconds`, `max_children`, `max_depth`, `max_tokens_per_response`. |
| `output_contract` | JSON Schema for the result passed to `complete_task`. |
| `memory` | Memory settings (off by default; `MEMORY.md`). |

Example (illustrative; final field names fixed in Phase 1):

```toml
name = "reviewer"
role = "Read-only code reviewer. Give it file paths and the invariants that must hold; it reports findings with file:line evidence."
model = "claude-opus-5-5"
effort = "medium"
thinking_display = "updates"
tools = ["list_dir", "read_file", "search", "complete_task", "request_capability"]
grants = ["fs:read:workspace:/**"]
can_delegate_to = []

[limits]
max_turns = 30
max_tool_calls = 60
max_tokens_total = 400_000
max_wall_seconds = 900

[output_contract]
schema = "schemas/review_report.json"
```

## 3. AgentInstance: the running agent

| Field | Meaning |
|---|---|
| `instance_id` | UUIDv7 |
| `spec_ref` | `name@hash` |
| `run_id`, `task_id` | What it is working on |
| `parent_instance_id` | Who created it (null for the root) |
| `capabilities` | **Effective** capabilities: `spec.grants ∩ capabilities passed by parent` (+ approved escalations) |
| `transcript` | Append-only list of messages (derived from journal; never edited) |
| `status` | `created → running → (waiting_tool \| waiting_approval \| waiting_children) → completed \| failed \| cancelled` |
| `usage` | Tokens in/out/cache, turns, tool calls, wall time, cost estimate |

The instance holds no hidden state: everything above is a projection of journal events.

### Why the transcript must be append-only

Opus 5.5 binds each thinking block to the exact prefix of the conversation that produced it. Editing, deleting, or reordering earlier turns, or rebuilding the system prompt or tool array, invalidates later thinking blocks, which is a 400 error on newly created accounts (RESEARCH §2.2). The same rule maximises prompt-cache hits. So the Model Gateway checks, before every request, that the new `messages` array starts with the previous request's array byte-for-byte (the **prefix invariant**). A violation is a kernel bug and fails the run loudly.

What we do instead of editing history:

| Need | Append-only form |
|---|---|
| New instruction for the agent mid-run | Append a `role: "system"` message (supported on Opus 5.5) |
| A one-turn reminder (e.g. budget remaining) | Append a text block after the tool results in the same user message; leave earlier copies in place |
| Context too long | Server-side compaction or context editing, or "simple compaction": start a **new** instance with a summary (MEMORY.md) |
| Change tools | Declare all tools at start; later, the `tool_addition`/`tool_removal` beta |

## 4. Termination: explicit, not implicit

A model response with `stop_reason: "end_turn"` only means "I stopped writing". It does not tell us whether the task succeeded. So every agent gets a kernel tool:

```
complete_task(status: "succeeded" | "failed" | "blocked", result: <output_contract>, summary: str)
```

`strict: true` makes the arguments schema-valid. Because Opus 5.5 rejects forced tool choice, the system prompt tells the agent to finish by calling `complete_task`. If the agent ends a turn without calling it, the kernel appends one user message ("You ended without calling complete_task. Call it now with your result or status blocked.") and gives one more turn. A second miss is a typed failure `no_completion`. Both events are journaled, which makes "agent forgot to finish" measurable.

## 5. Task model

A task is a unit of work with an owner, a contract, and a lifecycle. It exists independently of the agent working on it, so it can be retried with a different agent.

| Field | Meaning |
|---|---|
| `task_id`, `parent_task_id`, `root_task_id` | Tree structure |
| `created_by` | Actor: human, kernel, or agent instance |
| `objective` | What must be achieved, in one or two sentences |
| `brief` | Everything the assignee is told: context, constraints, relevant decisions already made |
| `inputs` | References to workspace artifacts (paths + content hashes at creation time) |
| `acceptance` | Checkable criteria (list of strings; may reference a deterministic checker) |
| `output_schema` | Schema of the expected result |
| `assignee_spec` | Which agent spec runs it |
| `budget` | Token/turn/time budget carved out of the parent's remaining budget |
| `status` | `pending → running → succeeded \| failed \| cancelled \| budget_exhausted` (+ `blocked` when the assignee reports it cannot proceed) |
| `result` / `failure` | The `complete_task` payload, or a typed failure (section 9) |

State transitions are events (`task.status_changed` with `from`, `to`, `reason`). The task tree of a run is a projection.

## 6. Delegation

Delegation is a kernel tool, not a conversation between agents:

```
delegate(agent: <name from roster>, objective, brief, inputs[], acceptance[], budget?)
  → { task_id, status, result | failure, summary, usage }
```

What the kernel does:

1. Check the caller has `agent:delegate:<name>`, the depth limit, and the fan-out limit.
2. Create a child task and carve its budget out of the parent's remaining budget (a child can never spend more than its parent has left).
3. Compute child capabilities: `child_spec.grants ∩ caller.capabilities`. Delegation can only **attenuate** rights, never amplify them.
4. Create a child instance with a **fresh context**: its own system prompt, and a first user message built from the brief and inputs. Nothing else from the parent's transcript crosses.
5. Phase 2: the parent's tool call waits until the child finishes (synchronous). Several `delegate` calls in one assistant turn run concurrently. Later: asynchronous delegation (`spawn` returns a handle immediately; a separate `await_tasks` tool waits), which Anthropic's migration guide reports (for Claude Fable 5.1) lowers time to completion when the lead has other work to do meanwhile. Not yet verified for Opus 5.5.
6. The child's `complete_task` payload, plus status and usage, returns to the parent as the `tool_result`.

### Context transfer is the experiment

Because the brief is the *only* channel, the kernel can measure exactly what crossed each boundary: brief length, which input artifacts were referenced, and later (with a judge) which facts the child needed but did not receive. That turns the Cognition-vs-Anthropic debate (RESEARCH §3) into an experiment with three variants:

- **brief-only**: the parent writes a self-contained brief (Anthropic's research-system pattern);
- **brief + summary**: the kernel appends a compacted summary of the parent's transcript;
- **full trace**: the child receives the parent's full visible transcript as a document (Cognition's principle 1), thinking blocks excluded because they cannot cross conversations.

## 7. Communication

**Hub-and-spoke through the kernel.** Agents never message each other directly. Every interaction is a kernel call that becomes an event:

| Interaction | Mechanism |
|---|---|
| Ask another agent to do work | `delegate` (task + brief) |
| Return work | `complete_task` result |
| Share artifacts | Files in the run workspace; every write is an event with the new content hash |
| Ask a human | `ask_human(question)` (Phase 2) → approval-style queue |

Why not direct agent-to-agent messaging: it creates conversations nobody owns, makes causality hard to trace, and is the main ingredient of "agents agreeing with each other" designs. It can be added later as an experiment (a `post`/`read` bulletin board tool or peer messages) and compared against hub-and-spoke on the same tasks.

**Shared workspace write conflicts.** Workspace writes carry the content hash the agent last read; a write against a stale hash is rejected (`stale_write`), the same idea as Claude Code's edit tool refusing to write a file that changed since it was read. Concurrent writers to the same file are a known multi-agent failure mode (Cognition's principle 2); the conflict events make it visible.

## 8. Loop and runaway prevention

Every limit is enforced by the kernel and produces an event plus a typed failure.

| Risk | Guard |
|---|---|
| Endless tool loop | `max_turns`, `max_tool_calls` per instance |
| Token blow-up | `max_tokens_total` per instance; task budgets carved from the parent; run-level hard cap in dollars (computed from usage × list price) |
| Hanging | `max_wall_seconds` per instance; per-tool timeouts |
| Repeating the same failing call | Detector: same tool + same canonical input + same error ≥ 3 times → inject a system note once, then fail `repetition` |
| Delegation explosion | `max_depth` (default 2), `max_children` per instance, run-wide max instances |
| Delegation cycles | A spec cannot delegate a task whose `(spec, objective hash)` already appears among its ancestors |
| Agent forgetting to stop | `complete_task` rule (section 4) |
| Model pacing | Optional Anthropic `task_budget` (beta) so the model itself sees a token countdown; advisory only, the kernel limits remain authoritative |

Budget reminders to the agent are appended (never edited) text blocks after tool results, for example "Budget: 31% of tokens remaining."

## 9. Failures

Failures are typed values carried in events and task results, never just strings.

| Category | Types | Origin |
|---|---|---|
| `model` | `api_error`, `rate_limited`, `overloaded`, `timeout`, `refusal` (+ category), `max_tokens`, `context_exceeded`, `prefix_invariant_violation` | Model Gateway |
| `tool` | `invalid_input`, `execution_error`, `timeout`, `output_too_large`, `stale_write`, `not_found` | Syscall layer / handler |
| `policy` | `permission_denied`, `approval_rejected`, `approval_timeout` | Policy engine / approval gate |
| `sandbox` | `container_error`, `resource_limit`, `violation` (blocked network/file access) | Sandbox backend |
| `limit` | `max_turns`, `max_tool_calls`, `budget_exhausted`, `wall_time`, `depth`, `fan_out`, `repetition`, `cycle` | Scheduler |
| `agent` | `no_completion`, `reported_failure`, `reported_blocked`, `output_contract_violation` | Runtime |
| `delegation` | `child_failed` (wraps the child's failure) | Kernel |
| `kernel` | `internal_error`, `crash_recovery` | Kernel |

**Propagation.** A failure stops at the first level that can handle it:

1. Tool failures go back to the model as `is_error` tool results. The model decides what to do (retry differently, use another tool, report blocked). This is the main recovery path and the one experiments observe.
2. A child task failure returns to the parent agent as a `delegate` result with `status: failed` and the typed failure. The parent may re-delegate, do it itself, or fail.
3. Limit, kernel, and unrecoverable model failures end the instance; its task fails; the failure propagates up as in (2).
4. The run fails only when the root task fails.

## 10. Retries

| Layer | Who retries | What | Never |
|---|---|---|---|
| Transport | Kernel (Model Gateway), with backoff, `retry-after` respected | 429, 5xx, connection errors, `overloaded` | 400s; refusals (fallback is separate) |
| Tool execution | Kernel, only for tools marked `idempotent` | Transient errors (timeouts, container start failure) | Non-idempotent tools (writes, exec); `permission_denied`; `invalid_input` |
| Tool semantics | The model, after an `is_error` result | Anything it can fix by changing its call | — |
| Task | The parent agent (model-driven) or an orchestration policy | Re-delegate, possibly to another spec | Same spec + same brief more than once without change |

The SDK's built-in retries are disabled (`max_retries=0`) so every attempt is a journaled `model.retry` event. **Crash recovery:** on restart, the kernel replays the journal; a `tool.call_requested` without a matching outcome is *in doubt*. Idempotent tools are re-run; others are reported to the model as `{"error": "interrupted", "effect_unknown": true}`.

Refusals: Opus 5.5 may return `stop_reason: "refusal"`. With server-side fallbacks enabled (D-013), the API may answer from another model; the gateway records the model that actually responded so experiment analysis can flag those runs.

## 11. Scaling from one agent to many

The same kernel objects cover every stage; nothing is rebuilt.

| Stage | What changes |
|---|---|
| One agent, no tools | Just the loop (E000) |
| One agent with tools | Syscall layer, policy, journal (Phase 1) |
| Static pipeline (researcher → builder → reviewer) | Code-defined orchestration: the kernel creates tasks in sequence; each agent is a single instance (Phase 2) |
| LLM orchestrator | A lead spec with `can_delegate_to` and the `delegate` tool (Phase 2) |
| Parallel workers | Several `delegate` calls per turn, run concurrently (Phase 2) |
| Async delegation, peer messaging, blackboard | Additional kernel tools, each introduced by an experiment (Phase 5) |

Rule from the vision: every role must justify itself. The experiment catalogue compares each multi-agent design against a single agent **with the same total token budget**.
