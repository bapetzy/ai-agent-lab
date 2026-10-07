"""One run = one task, one agent, one workspace, one journal record from start to end."""

from __future__ import annotations

import platform
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import agentlab
from agentlab.agent import AgentOutcome, run_agent
from agentlab.budget import Budget
from agentlab.gateway import ModelCallFailed, ModelGateway
from agentlab.journal import Journal, Recorder, new_id
from agentlab.kernel import Approver, Kernel
from agentlab.model_types import ModelBackend
from agentlab.task import Task, evaluate_checks
from agentlab.tools import TOOLS
from agentlab.workspace import Workspace

SCHEMA_VERSION = 2  # 1 = Phase 1 (Anthropic-shaped payloads); 2 = provider-neutral


@dataclass(frozen=True)
class RunResult:
    run_id: str
    agent_id: str
    task_id: str
    status: str  # completed | failed
    reason: str | None
    final_text: str
    success: bool | None  # all checks passed? None if the run did not complete
    workspace: Path
    usage: dict


def start_run(
    task: Task,
    journal: Journal,
    backend: ModelBackend,
    *,
    approver: Approver,
    runs_dir: Path,
    ids: tuple[str, str, str] | None = None,
    workspace: Workspace | None = None,
    extra: dict | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> RunResult:
    """Run ``task`` to the end on ``backend``. ``ids``/``workspace`` are only given by replay
    and rerun."""
    run_id, agent_id, task_id = ids or (new_id(), new_id(), new_id())
    rec = Recorder(journal, run_id, agent_id, task_id)
    prices = backend.prices(task.model["name"])  # None = free; may refuse an unpriced model
    budget = Budget(task.limits, prices=prices, clock=clock)
    ws = workspace or Workspace.create(
        task.workspace_fixture, Path(runs_dir) / "workspaces" / run_id
    )
    tools = {name: TOOLS[name] for name in task.agent["tools"]}
    specs = tuple(t.spec() for t in tools.values())

    rec.emit(
        "run_started",
        {
            "schema_version": SCHEMA_VERSION,
            "task": {"name": task.name, "prompt": task.prompt, "sha256": task.source_sha256},
            "task_source": task.source,
            "model": task.model,
            "model_requested": task.model["name"],
            "backend": backend.describe(),
            "fallback": "disabled",
            "prices_per_mtok": budget.prices,
            "agent": {"system": task.agent["system"], "tools": task.agent["tools"]},
            "tools": [
                {"name": s.name, "description": s.description, "parameters": s.parameters}
                for s in specs
            ],
            "limits": task.limits.to_dict(),
            "policy": task.policy.to_list(),
            "checks": task.checks,
            "workspace": str(ws.root),
            "fixture": ws.snapshot(),
            "fixture_blob": rec.blob(ws.read_all()),
            "versions": {"agentlab": agentlab.__version__, "python": platform.python_version()},
            **(extra or {}),
        },
    )
    started = time.perf_counter()
    gateway = ModelGateway(backend, rec, sleep=sleep)
    kernel = Kernel(rec, ws, task.policy, approver, tools)
    try:
        outcome = run_agent(
            gateway=gateway,
            kernel=kernel,
            budget=budget,
            rec=rec,
            model=task.model,
            system=task.agent["system"],
            tools=specs,
            prompt=task.prompt,
        )
    except ModelCallFailed as e:
        outcome = AgentOutcome("failed", f"model:{e.reason}", "")
    except Exception as e:  # a kernel bug or a replay divergence: record it, do not hide it
        outcome = AgentOutcome("failed", f"kernel_error:{type(e).__name__}: {e}", "")
        rec.emit(
            "run_failed",
            {
                "reason": outcome.reason,
                "traceback": traceback.format_exc(),
                "usage": budget.snapshot(),
            },
        )
        return RunResult(
            run_id,
            agent_id,
            task_id,
            "failed",
            outcome.reason,
            "",
            None,
            ws.root,
            budget.snapshot(),
        )

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    success = None
    if outcome.status == "completed":
        results = evaluate_checks(task.checks, ws, outcome.final_text)
        for r in results:
            rec.emit("check_result", r)
        success = all(r["passed"] for r in results)
        rec.emit(
            "run_completed",
            {
                "final_text": outcome.final_text,
                "success": success,
                "usage": budget.snapshot(),
                "duration_ms": duration_ms,
                "workspace_after": ws.snapshot(),
            },
        )
    else:
        rec.emit(
            "run_failed",
            {
                "reason": outcome.reason,
                "final_text": outcome.final_text,
                "usage": budget.snapshot(),
                "duration_ms": duration_ms,
                "workspace_after": ws.snapshot(),
            },
        )
    return RunResult(
        run_id,
        agent_id,
        task_id,
        outcome.status,
        outcome.reason,
        outcome.final_text,
        success,
        ws.root,
        budget.snapshot(),
    )
