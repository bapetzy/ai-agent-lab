"""What can we reproduce from the journal? Three different things, kept apart on purpose.

1. Inspection / reconstruction (``reconstruct_transcript``, ``agent-lab trace``):
   rebuild what happened from recorded events and blobs. Nothing is executed.

2. Deterministic re-execution (``replay_run``, ``agent-lab replay``):
   run the *current* kernel code again, but feed it the recorded inputs instead of the
   outside world: recorded model responses, recorded human decisions, the recorded
   workspace fixture. Each request the kernel builds must be byte-identical to the
   recorded one, and the new events must match the recorded events. If they do, the
   kernel's behaviour for this run is reproduced exactly. Tool calls are really executed
   again (in a fresh temporary workspace), which checks that the tools are deterministic.
   The model is NOT called. This proves nothing about the model; it tests the kernel.

3. Model re-execution (``agent-lab rerun``): send the same task to the real model again.
   That is a NEW run with a new sample from the model. It is not replay.

Not reproduced by (2): timestamps, durations, event ids, API request ids, transient API
errors and their retries (only successful responses are served), and anything the model
would do differently if asked again.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from anthropic.types import Message

from agentlab.journal import Journal, canonical_json
from agentlab.run import start_run
from agentlab.task import Task
from agentlab.workspace import Workspace

# Payload fields that legitimately differ between the original run and its replay.
VOLATILE = {"duration_ms", "request_id", "workspace", "model_backend", "versions", "traceback"}

NOT_REPRODUCED = [
    "timestamps and durations (recorded, not re-measured)",
    "event ids and API request ids",
    "transient API errors and retries (model_retry events are skipped)",
    "model sampling: responses are served from the recording, the model is not called",
]


class ReplayDivergence(Exception):
    pass


class ReplayClient:
    """Serves recorded responses, but only if the kernel asks exactly the recorded question."""

    def __init__(self, requests: list[dict], responses: list[dict]):
        self.recorded_requests = requests
        self.recorded_responses = responses
        self.messages = self
        self.calls = 0
        self.identical = 0

    def create(self, **params) -> Message:
        i = self.calls
        self.calls += 1
        if i >= len(self.recorded_responses):
            raise ReplayDivergence(f"request {i + 1}: the recording has no response for it")
        if canonical_json(params) != canonical_json(self.recorded_requests[i]):
            raise ReplayDivergence(
                f"request {i + 1} differs from the recorded request: "
                f"{_first_difference(self.recorded_requests[i], params)}"
            )
        self.identical += 1
        return Message.model_validate(self.recorded_responses[i])


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


def _normalize(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in VOLATILE}


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

    requests = [
        journal.get_blob(e.payload["request_blob"]) for e in recorded if e.type == "model_request"
    ]
    responses = [
        journal.get_blob(e.payload["response_blob"]) for e in recorded if e.type == "model_response"
    ]
    decisions = [
        {k: v for k, v in e.payload.items() if k != "tool_use_id"}
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
    client = ReplayClient(requests, responses)
    scratch = Journal(":memory:")  # replay never writes to the real journal
    first = recorded[0]
    result = start_run(
        task,
        scratch,
        client,
        approver=recorded_approver,
        runs_dir=work_dir,
        model_backend="replay",
        prices=started["prices_per_mtok"],
        ids=(run_id, first.agent_id, first.task_id),
        workspace=workspace,
        sleep=lambda s: None,
    )
    report.identical_requests = client.identical
    replayed = scratch.events(run_id)
    report.replay_status = replayed[-1].type

    if result.status == "failed" and result.reason and "ReplayDivergence" in result.reason:
        report.differences.append(result.reason.split("ReplayDivergence: ", 1)[-1])

    for i, (old, new) in enumerate(zip(recorded, replayed, strict=False)):
        report.events_compared += 1
        if old.type != new.type:
            report.differences.append(f"event {i + 1}: type {old.type} -> {new.type}")
            break
        if canonical_json(_normalize(old.payload)) != canonical_json(_normalize(new.payload)):
            changed = sorted(
                k
                for k in set(old.payload) | set(new.payload)
                if k not in VOLATILE and old.payload.get(k) != new.payload.get(k)
            )
            report.differences.append(f"event {i + 1} ({old.type}): {', '.join(changed)} differ")
            break
    if len(recorded) != len(replayed) and not report.differences:
        report.differences.append(f"event count {len(recorded)} -> {len(replayed)}")
    report.matched = not report.differences
    return report


def reconstruct_transcript(journal: Journal, run_id: str) -> dict:
    """The full conversation as the model last saw it, plus its last answer."""
    events = journal.events(run_id)
    last_request = next(e for e in reversed(events) if e.type == "model_request")
    body = journal.get_blob(last_request.payload["request_blob"])
    responses = [e for e in events if e.type == "model_response"]
    messages = list(body["messages"])
    if responses and responses[-1].payload["turn"] == last_request.payload["turn"]:
        final = journal.get_blob(responses[-1].payload["response_blob"])
        messages.append({"role": "assistant", "content": final["content"]})
    return {"system": body.get("system"), "tools": body.get("tools"), "messages": messages}
