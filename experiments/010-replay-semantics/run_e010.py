"""E010: what can we reproduce from the journal? Offline: no API key, no network, no model.

uv run python experiments/010-replay-semantics/run_e010.py
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from agentlab import tools
from agentlab.backends.scripted import ScriptedBackend, load_script
from agentlab.journal import Journal
from agentlab.kernel import approve_all
from agentlab.model_types import ModelError
from agentlab.policy import Decision, Policy
from agentlab.replay import reconstruct_transcript, replay_run
from agentlab.run import start_run
from agentlab.task import Task
from agentlab.trace import format_trace

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "tasks" / "demo-permissions"
OUT = Path(__file__).resolve().parent / "results" / "e010-results.md"


def record(journal: Journal, work: Path, task: Task, script: list) -> str:
    result = start_run(
        task,
        journal,
        ScriptedBackend(script),
        approver=approve_all,
        runs_dir=work / "runs",
        sleep=lambda s: None,
    )
    return result.run_id


@contextmanager
def patched(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="e010-"))
    fixture_copy = work / "fixture-task"
    shutil.copytree(DEMO, fixture_copy)  # a task copy we are allowed to edit
    task = Task.load(fixture_copy)
    script = load_script(DEMO / "scripted.json")
    rows = []

    def case(name, expected, run_id, journal, change=None):
        if change is None:
            report = replay_run(journal, run_id, work_dir=work / f"replay-{len(rows)}")
        else:
            with change():
                report = replay_run(journal, run_id, work_dir=work / f"replay-{len(rows)}")
        observed = "MATCH" if report.matched else "DIVERGED: " + "; ".join(report.differences)
        rows.append((name, expected, observed, f"{report.identical_requests}/{report.model_calls}"))

    journal = Journal(":memory:")
    base = record(journal, work, task, script)

    # R1 inspection
    transcript = reconstruct_transcript(journal, base)
    trace = format_trace(journal, base)
    rows.append(
        (
            "R1 inspection: rebuild transcript + trace from journal only",
            "possible",
            f"{len(transcript['messages'])} messages, {len(trace.splitlines())} trace lines",
            "-",
        )
    )

    # R2 unchanged kernel
    case("R2 unchanged kernel", "MATCH", base, journal)

    # R3a policy changed
    case(
        "R3a policy engine changed (allows everything)",
        "DIVERGED at permission_checked",
        base,
        journal,
        lambda: patched(Policy, "check", lambda self, c, p: Decision("allow", "changed")),
    )

    # R3b tool description changed
    changed_desc = dataclasses.replace(tools.TOOLS["list_files"], description="Other words.")
    case(
        "R3b tool description changed",
        "DIVERGED at request 1",
        base,
        journal,
        lambda: patched_item(tools.TOOLS, "list_files", changed_desc),
    )

    # R3c tool implementation changed (list_files returns reverse order)
    orig = tools.TOOLS["list_files"]
    reversed_impl = dataclasses.replace(
        orig,
        run=lambda ws, a: tools.ToolOutput(
            "\n".join(reversed(ws.list_files(a["path"]))), {"count": 0}
        ),
    )
    case(
        "R3c tool implementation changed",
        "DIVERGED at tool_completed",
        base,
        journal,
        lambda: patched_item(tools.TOOLS, "list_files", reversed_impl),
    )

    # R4a fixture edited on disk after the run
    (fixture_copy / "workspace" / "notes" / "meeting.md").write_text(
        "EDITED LATER", encoding="utf-8"
    )
    case("R4a fixture files edited after the run", "MATCH (recorded copy used)", base, journal)

    # R4b task file edited after the run
    toml = fixture_copy / "task.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            "Use only the provided tools.", "EDITED SYSTEM PROMPT."
        ),
        encoding="utf-8",
    )
    case("R4b task.toml edited after the run", "MATCH (recorded config used)", base, journal)

    # R5 transient API error during the original run
    j5 = Journal(":memory:")
    rate = ModelError("transient", "slow down", status=429)
    r5 = record(j5, work, Task.load(DEMO), [rate, *script])
    case("R5 original run had a transient error + retry", "MATCH (retry skipped)", r5, j5)

    # R6 permanent API error ended the original run
    j6 = Journal(":memory:")
    bad = ModelError("permanent", "bad request", status=400)
    r6 = record(j6, work, Task.load(DEMO), [script[0], bad])
    case("R6 original run died on a permanent error", "DIVERGED (no recorded response)", r6, j6)

    # R7 model re-execution
    rows.append(
        (
            "R7 ask the real model again",
            "impossible offline; is a NEW run",
            "not run: no model available (local backend not built yet; `agent-lab rerun <run>`)",
            "-",
        )
    )

    lines = [
        "# E010 results (generated by run_e010.py; schema v2, provider-neutral)",
        "",
        "| Case | Expected | Observed | identical requests |",
        "|---|---|---|---|",
    ]
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    shutil.rmtree(work, ignore_errors=True)


@contextmanager
def patched_item(mapping, key, value):
    old = mapping[key]
    mapping[key] = value
    try:
        yield
    finally:
        mapping[key] = old


if __name__ == "__main__":
    main()
