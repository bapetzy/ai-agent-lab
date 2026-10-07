# Observability, events, and replay

> **Implementation status (Phase 1).** IMPLEMENTED: the journal with envelope `run_id, seq, event_id (UUIDv7), ts, type, agent_id, task_id, payload` and snake_case event types (`run_started`, `model_request`, `model_response`, `model_retry`, `model_error`, `tool_requested`, `permission_checked`, `approval_requested`, `approval_resolved`, `permission_approved`, `permission_denied`, `tool_started`, `tool_completed`, `tool_failed`, `limit_reached`, `check_result`, `run_completed`, `run_failed`); full request/response bodies as blobs; inspection (`trace`, `show`); deterministic re-execution (`replay`), semantics measured in E010 (D-024). NOT IMPLEMENTED: causation/correlation/span ids (a tool call's events share `tool_use_id`), projections tables, fork, progress-update display, OTel export. The catalogue below uses Phase 0 dotted names; the implemented names are the snake_case list above (D-023).

This document answers: how events are represented, what is recorded, how executions are replayed and forked, and how we inspect agent behaviour without relying on hidden reasoning.

## 1. Principle

The journal is the system. Every state change in the lab is the result of an event, and every view (run status, task tree, the Observatory) is computed from events. If something is not in the journal, it did not happen, and it cannot be replayed.

This is **event sourcing**: store the facts that happened, derive the current state by folding over them. We choose it because the questions we want to answer are about history ("what did the reviewer see before it approved?"), not just about current state.

## 2. Event envelope

```python
class Event(BaseModel):
    event_id: UUID            # UUIDv7: time-ordered, unique
    run_id: UUID
    seq: int                  # strictly increasing per run, assigned by the journal
    ts: datetime              # wall clock (recorded, so replay does not read the clock)
    type: str                 # e.g. "tool.completed"
    schema_version: int
    actor: Actor              # {kind: agent|human|kernel|tool|model, id}
    task_id: UUID | None
    instance_id: UUID | None
    causation_id: UUID | None # the event that directly caused this one
    correlation_id: UUID | None  # groups a logical operation, e.g. one tool call's lifecycle
    span_id: str | None       # for timeline rendering and later OTel export
    parent_span_id: str | None
    payload: dict             # typed per event type (Pydantic model per type)
    blob_refs: list[str]      # SHA-256 of large payloads stored in the blob table
```

- `causation_id` builds a causal graph: `tool.completed` ← `tool.started` ← `permission.decided` ← `tool.call_requested` ← `model.response` ← `model.request`. "Why did this happen?" is answered by walking it backwards.
- `correlation_id` groups everything for one tool call, one delegation, or one approval, so the Observatory can draw it as one span.
- Large data (full API requests and responses, file contents, tool outputs above a size threshold) live in content-addressed blobs. Events stay small and queryable.

## 3. Event catalogue (initial)

| Group | Types |
|---|---|
| Run | `run.started` (task, spec hashes, policy hash, kernel version, model config, memory snapshot), `run.completed`, `run.failed`, `run.forked` |
| Task | `task.created`, `task.status_changed` |
| Instance | `instance.created` (spec, effective capabilities, budget), `instance.status_changed`, `instance.limit_hit` |
| Model | `model.request` (blob: full body; token estimate; prefix-check result), `model.response` (blob; `stop_reason`; usage incl. cache reads; actual responding model; refusal details), `model.retry`, `model.progress_update` (non-empty progress thinking text under `display: "updates"`) |
| Tool | `tool.call_requested` (raw input), `tool.input_resolved`, `tool.started`, `tool.completed` (output blob, truncated flag, duration), `tool.failed` (typed failure) |
| Permission | `permission.decided` (decision, required capabilities, matched rule), `capability.requested`, `capability.granted`, `capability.denied` |
| Approval | `approval.requested`, `approval.resolved` (who, decision, message, latency) |
| Delegation | `delegation.requested`, `delegation.completed` (links parent call ↔ child task) |
| Workspace | `workspace.file_written` (path, old hash, new hash), `workspace.write_rejected` (stale hash) |
| Sandbox | `sandbox.started`, `sandbox.exited` (exit code, duration, peak memory if available), `sandbox.violation` |
| Budget | `budget.updated` (periodic snapshot per instance), `budget.exhausted` |
| Memory (Phase 4) | `memory.read`, `memory.written` (with provenance) |
| Human | `human.message`, `human.interrupt` |

## 4. Answering the questions

| Question | Where the answer comes from |
|---|---|
| WHAT happened? | Event `type` + `payload` |
| WHEN? | `ts` (wall clock) and `seq` (order) |
| WHICH agent caused it? | `actor`, `instance_id` → `instance.created` → spec name@hash |
| WHAT tool was used? | `tool.call_requested.payload.tool` + tool version |
| WHAT input? | `tool.call_requested` (raw) and `tool.input_resolved` (canonical) |
| WHAT output? | `tool.completed` blob, plus the exact `tool_result` in the next `model.request` blob |
| WHAT task was active? | `task_id` → `task.created` |
| WHAT state changed? | `task.status_changed`, `instance.status_changed`, `workspace.file_written` (diff from blobs) |
| WHO delegated? | `delegation.requested.actor` and the task tree (`parent_task_id`) |
| WHY did the workflow continue? | The causal chain: which `model.response` contained which `tool_use`; which `permission.decided` rule allowed it; which `approval.resolved` unblocked it. Plus the model's own stated intent (section 6), labelled as model-reported. |
| WHEN did it fail? | First `*.failed` / `limit_hit` event and its `causation_id` chain |
| HOW was it recovered? | Events after the failure in the same instance or its parent: the next `tool_use` after an `is_error` result, a re-delegation, a `model.retry` |

Example (SQLite, illustrative):

```sql
-- every denied tool call in a run, with the rule that denied it
SELECT e.seq, json_extract(e.payload, '$.tool') AS tool,
       json_extract(e.payload, '$.matched_rule') AS rule
FROM events e
WHERE e.run_id = ? AND e.type = 'permission.decided'
  AND json_extract(e.payload, '$.decision') = 'deny'
ORDER BY e.seq;
```

## 5. Replay

Three distinct operations, often confused:

### 5.1 Inspect (read-only replay)

Rebuild any view at any point in time by folding events up to `seq = N`. "Show me the task tree and every agent's transcript as of step 214." No execution. This is what the Observatory's timeline scrubber does.

### 5.2 Re-execution (deterministic replay)

Run the kernel again, but serve every non-deterministic input from the journal instead of the outside world:

- Model Gateway in **replay mode**: when the kernel produces request *k* for an instance, the gateway compares it byte-for-byte with the recorded request *k* and returns the recorded response. A mismatch raises `ReplayDivergence` with a diff.
- Tool executors return recorded outcomes; approvals return recorded decisions; clock and ID generation read recorded values.

If the kernel is correct and deterministic, the run reproduces exactly, for free (no API calls). Uses:

- **Regression tests**: recorded runs become golden fixtures; a kernel change that alters what the model would see fails the test.
- **Debugging**: step through a past run in a debugger.

### 5.3 Fork (counterfactual)

Replay deterministically up to event *N*, then switch the gateway and executors to **live** mode with a change: a different policy, a stricter reviewer prompt, a tool failure injected, a different human decision, a different model for one agent. The fork is a new run with `run.forked {parent_run_id, fork_seq, changes}`. Comparing parent and fork aligned by step is the core of many experiments.

Fork constraint from the API (RESEARCH §2.2): changes that alter **future** inputs (a different approval, a different tool result, a later system message) keep earlier transcripts valid. Changes to an instance's **past** (its system prompt or tool set) mean that instance's transcript prefix changed; its thinking blocks become invalid. In that case the fork restarts that instance from its first message, or uses `prefix_mismatch_behavior: "drop_block"`, and the fork records which one it did.

### Why re-execution is not "running it again"

Calling the live model with the same input gives a new sample, not a replay. LangGraph-style "replay from checkpoint" re-executes later nodes, including LLM calls, so results can differ (LangGraph docs). We separate the two: replay is exact and offline; a live re-run is a new run and part of an experiment's repetitions.

## 6. Inspecting behaviour without the chain of thought

Opus 5.5 never returns raw reasoning, and prompts that try to extract it may be refused (`reasoning_extraction`). That is fine, because reasoning text would be weak evidence anyway: it is model output, not a guaranteed account of what produced the action. We rely instead on layers of evidence, strongest first:

| Evidence | Strength | Notes |
|---|---|---|
| **Actions** (tool calls with inputs, in order) | Ground truth of what the agent did | Fully captured |
| **Effects** (file diffs, sandbox results, delegations, approvals) | Ground truth of what changed | Fully captured |
| **Context** (exact request the model saw at each step) | Ground truth of what the agent could have known | Full request blobs; the Observatory shows per-step deltas |
| **Decision points** (policy decisions, approvals, budget hits, errors returned) | Ground truth of the constraints acting on the agent | Captured as events |
| **Structured outputs** (`complete_task` result, delegation briefs) | Model's committed claims, checkable against effects | Schema-validated |
| **Progress updates** (`thinking.display: "updates"`, beta) | Model's own short notes between tool calls | Model-reported; may be incomplete or wrong; useful for "what did it think it was doing" |
| **Reasoning summaries** (`display: "summarized"`) | A summary of hidden reasoning | Model-generated summary; label as such; used only in specific experiments |
| Optional `intent` argument on tools | Model states why it calls the tool | Experiment E016 tests whether this helps or hurts; off by default |

Analyses compare layers: does the stated intent match the action? Did the agent claim success (`complete_task`) while the effects show otherwise? That gap is itself one of the most interesting measurements (MAST's "task verification" failures).

## 7. Metrics derived from events

Per run, per instance, per spec: tokens (input, output, cache read/write), estimated cost, wall time, turns, tool calls by tool and outcome, error rate, recoveries (error followed by a different successful call), denials, approval count and latency, delegation count and depth, brief sizes, compaction count, limit hits. All are projections; no metric is collected outside the journal.

## 8. External formats

- **OpenTelemetry**: optional exporter later. Mapping: run/task → trace; instance → `invoke_agent` span; `model.request/response` → model call span; tool lifecycle → `execute_tool` span. The GenAI conventions are still in Development status (RESEARCH §5), so the exporter is a projection that can change without touching the journal.
- **JSONL export**: `lab export <run>` → one event per line + blob files. Used for cassettes and sharing.
