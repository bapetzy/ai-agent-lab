"""What can we reproduce from the journal? Three different things, kept apart on purpose.

1. Inspection / reconstruction (``reconstruct_transcript``, ``agent-lab trace``):
   rebuild what happened from recorded events and blobs. Nothing is executed.

2. Deterministic re-execution (``replay_run``, ``agent-lab replay``):
   run the *current* kernel code again, but feed it the recorded inputs instead of the
   outside world: recorded model responses, recorded human decisions, the recorded
   workspace fixture. Each request the kernel builds must equal the recorded one, and the
   new events must match the recorded events. If they do, the kernel's behaviour for this
   run is reproduced exactly. Tool calls are really executed again (in a fresh temporary
   workspace), which checks that the tools are deterministic. No model and no backend is
   called: this needs no API key, no local server, no network. It tests the kernel, not
   the model.

3. Model re-execution (``agent-lab rerun``): send the same task to the model again. That
   is a NEW run with a new sample from the model. It is not replay.

Recordings are provider-neutral (schema version 2): the same replay works whether the run
used the scripted backend, a local model or an external API. Phase 1 recordings (schema 1,
Anthropic-shaped) are refused.

Not reproduced by (2): timestamps, durations, wall-clock limits, event ids, API request ids,
transient errors and their retries (only successful responses are served), and anything the
model would do differently if asked again.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agentlab.journal import Journal, canonical_json
from agentlab.model_types import ModelRequest, ModelResponse
from agentlab.run import SCHEMA_VERSION, start_run
from agentlab.task import Task
from agentlab.workspace import Workspace

# Payload fields that legitimately differ between the original run and its replay
# (removed at any depth before comparing).
VOLATILE = {
    "duration_ms",
    "request_id",
    "workspace",
    "backend",
    "versions",
    "traceback",
    "elapsed_s",
    "wire_blob",
}

NOT_REPRODUCED = [
    "timestamps, durations and wall-clock limits (recorded, not re-measured)",
    "event ids and API request ids",
    "transient errors and retries (model_retry events are skipped)",
    "model sampling: responses are served from the recording, no model is called",
]


class ReplayDivergence(Exception):
    pass


class ReplayBackend:
    """Serves recorded responses, but only if the kernel asks exactly the recorded question."""

    name = "replay"

    def __init__(self, requests: list[dict], responses: list[dict], prices: dict | None):
        self.recorded_requests = requests
        self.recorded_responses = responses
        self._prices = prices
        self.calls = 0
        self.identical = 0

    def wire_request(self, request: ModelRequest) -> dict:
        return request.to_dict()

    def complete(self, request: ModelRequest) -> ModelResponse:
        i = self.calls
        self.calls += 1
        if i >= len(self.recorded_responses):
            raise ReplayDivergence(f"request {i + 1}: the recording has no response for it")
        current = request.to_dict()
        if canonical_json(current) != canonical_json(self.recorded_requests[i]):
            raise ReplayDivergence(
                f"request {i + 1} differs from the recorded request: "
                f"{_first_difference(self.recorded_requests[i], current)}"
            )
        self.identical += 1
        return ModelResponse.from_dict(self.recorded_responses[i])

    def describe(self) -> dict:
        return {"name": self.name, "recorded_responses": len(self.recorded_responses)}

    def prices(self, model: str) -> dict | None:
        return self._prices


def _first_difference(old: dict, new: dict) -> str:
    for key in sorted(set(old) | set(new)):
        if canonical_json(old.get(key)) != canonical_json(new.get(key)):
            if key == "messages" and isinstance(old.get(key), list):
                for j, (a, b) in enumerate(zip(old[key], new.get(key) or [], strict=False)):
                    if canonical_json(a) != canonical_json(b):
                        return f"messages[{j}] changed"
                return f"message count {len(old[key])} -> {len(new.get(key) or [])}"
            return f"field {key!r} changed"
    return "unknown difference"


def _strip_volatile(value):
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


@dataclass
class ReplayReport:
    run_id: str
    matched: bool = False
    model_calls: int = 0
    identical_requests: int = 0
    events_compared: int = 0
    differences: list[str] = field(default_factory=list)
    not_reproduced: list[str] = field(default_factory=lambda: list(NOT_REPRODUCED))
    original_status: str = ""
    replay_status: str = ""


def replay_run(journal: Journal, run_id: str, *, work_dir: Path | None = None) -> ReplayReport:
    recorded = [e for e in journal.events(run_id) if e.type != "model_retry"]
    started = recorded[0].payload
    report = ReplayReport(run_id)
    report.original_status = recorded[-1].type

    version = started.get("schema_version", 1)
    if version != SCHEMA_VERSION:
        report.differences.append(
            f"unsupported schema_version {version}: this run was recorded before the "
            f"provider-neutral refactor (schema {SCHEMA_VERSION}) and cannot be replayed; "
            "it can still be inspected with `agent-lab trace`/`show`"
        )
        return report

    requests = [
        journal.get_blob(e.payload["request_blob"]) for e in recorded if e.type == "model_request"
    ]
    responses = [
        journal.get_blob(e.payload["response_blob"]) for e in recorded if e.type == "model_response"
    ]
    decisions = [
        {k: v for k, v in e.payload.items() if k != "tool_call_id"}
        for e in recorded
        if e.type == "approval_resolved"
    ]
    report.model_calls = len(responses)

    def recorded_approver(_request: dict) -> dict:
        if not decisions:
            raise ReplayDivergence("the kernel asked a human more often than recorded")
        return decisions.pop(0)

    task = Task.from_dict(started["task_source"], workspace_fixture=Path("(recorded)"))
    work_dir = Path(work_dir or tempfile.mkdtemp(prefix="agentlab-replay-"))
    workspace = Workspace.from_files(journal.get_blob(started["fixture_blob"]), work_dir / run_id)
    backend = ReplayBackend(requests, responses, started["prices_per_mtok"])
    scratch = Journal(":memory:")  # replay never writes to the real journal
    first = recorded[0]
    result = start_run(
        task,
        scratch,
        backend,
        approver=recorded_approver,
        runs_dir=work_dir,
        ids=(run_id, first.agent_id, first.task_id),
        workspace=workspace,
        sleep=lambda s: None,
        clock=lambda: 0.0,  # wall-clock limits are not replayed
    )
    report.identical_requests = backend.identical
    replayed = scratch.events(run_id)
    report.replay_status = replayed[-1].type

    if result.status == "failed" and result.reason and "ReplayDivergence" in result.reason:
        report.differences.append(result.reason.split("ReplayDivergence: ", 1)[-1])

    for i, (old, new) in enumerate(zip(recorded, replayed, strict=False)):
        report.events_compared += 1
        if old.type != new.type:
            report.differences.append(f"event {i + 1}: type {old.type} -> {new.type}")
            break
        a, b = _strip_volatile(old.payload), _strip_volatile(new.payload)
        if canonical_json(a) != canonical_json(b):
            changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            report.differences.append(f"event {i + 1} ({old.type}): {', '.join(changed)} differ")
            break
    if len(recorded) != len(replayed) and not report.differences:
        report.differences.append(f"event count {len(recorded)} -> {len(replayed)}")
    report.matched = not report.differences
    return report


def reconstruct_transcript(journal: Journal, run_id: str) -> dict:
    """The full conversation as the model last saw it, plus its last answer (neutral form)."""
    events = journal.events(run_id)
    last_request = next(e for e in reversed(events) if e.type == "model_request")
    body = journal.get_blob(last_request.payload["request_blob"])
    responses = [e for e in events if e.type == "model_response"]
    messages = list(body["messages"])
    if responses and responses[-1].payload["turn"] == last_request.payload["turn"]:
        final = journal.get_blob(responses[-1].payload["response_blob"])
        messages.append(
            {"role": "assistant", "text": final["text"], "tool_calls": final["tool_calls"]}
        )
    return {"system": body.get("system"), "tools": body.get("tools"), "messages": messages}
