"""Command line: ``agent-lab <command>``. Run ``uv run agent-lab --help``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentlab import config
from agentlab.backends import BackendNotAvailable, make_backend, secrets_of
from agentlab.journal import Journal, new_id
from agentlab.kernel import Approver, approve_all, deny_all
from agentlab.replay import replay_run
from agentlab.run import start_run
from agentlab.task import Task
from agentlab.trace import format_trace, summarize
from agentlab.workspace import Workspace

DEFAULT_DB = config.PROJECT_ROOT / "runs" / "lab.db"
DEFAULT_RUNS = config.PROJECT_ROOT / "runs"


def cli_approver(request: dict) -> dict:
    print("\n  === APPROVAL NEEDED ===")
    print(f"  tool:       {request['tool']}")
    print(f"  capability: {request['capability']} on '{request['path']}' (rule: {request['rule']})")
    print(f"  input:      {json.dumps(request['input'], ensure_ascii=False)[:300]}")
    answer = input("  approve? [y/N] ").strip().lower()
    return {"approved": answer in ("y", "yes"), "by": "human (cli prompt)", "note": ""}


def _approver(mode: str) -> Approver:
    if mode == "yes":
        return approve_all
    if mode == "no":
        return deny_all
    return cli_approver if sys.stdin.isatty() else deny_all


def _backend(args, task: Task):
    """The backend for this run, or None after printing why it is not available."""
    try:
        backend = make_backend(task, scripted=args.scripted)
    except BackendNotAvailable as e:
        print(f"cannot start: {e}")
        return None
    if backend.name == "scripted":
        print(f"backend   SCRIPTED fake model (offline, free; responses from {args.scripted})")
    else:
        lim = task.limits
        print(
            f"backend   {backend.name.upper()} model={task.model['name']} "
            f"max_turns={lim.max_turns} max_wall={lim.max_wall_seconds}s fallback=disabled"
        )
    return backend


def _finish(journal: Journal, result) -> int:
    print(format_trace(journal, result.run_id))
    print(f"\nrun id    {result.run_id}")
    print(f"workspace {result.workspace}")
    return 0 if result.status == "completed" else 1


def cmd_run(args) -> int:
    task = Task.load(args.task)
    backend = _backend(args, task)
    if backend is None:
        return 2
    journal = Journal(args.db, secrets=secrets_of(backend))
    result = start_run(
        task, journal, backend, approver=_approver(args.approve), runs_dir=Path(args.runs_dir)
    )
    return _finish(journal, result)


def cmd_rerun(args) -> int:
    """A NEW run of a recorded task (same config, same starting files). Not a replay."""
    journal = Journal(args.db)
    run_id = journal.find_run(args.run)
    started = journal.events(run_id)[0].payload
    if started.get("schema_version", 1) != 2:
        print("this run was recorded before the provider-neutral refactor and cannot be rerun")
        return 2
    task = Task.from_dict(started["task_source"], workspace_fixture=Path("(recorded)"))
    backend = _backend(args, task)
    if backend is None:
        return 2
    journal = Journal(args.db, secrets=secrets_of(backend))
    ids = (new_id(), new_id(), new_id())
    ws = Workspace.from_files(
        journal.get_blob(started["fixture_blob"]), Path(args.runs_dir) / "workspaces" / ids[0]
    )
    result = start_run(
        task,
        journal,
        backend,
        approver=_approver(args.approve),
        runs_dir=Path(args.runs_dir),
        ids=ids,
        workspace=ws,
        extra={"rerun_of": run_id},
    )
    return _finish(journal, result)


def cmd_runs(args) -> int:
    journal = Journal(args.db)
    print(f"{'run':10} {'started (UTC)':20} {'task':24} {'backend':10} {'model':20} result")
    for started in journal.runs():
        s = summarize(journal, started.run_id)
        result = s["status"] + ("" if s["success"] is None else f" success={s['success']}")
        print(
            f"{started.run_id[-8:]:10} {started.ts[:19]:20} {s['task'][:24]:24} "
            f"{str(s['backend'])[:10]:10} {s['model_requested'][:20]:20} {result}"
        )
    return 0


def cmd_trace(args) -> int:
    journal = Journal(args.db)
    run_id = journal.find_run(args.run)
    if args.json:
        print(json.dumps(summarize(journal, run_id), indent=2, ensure_ascii=False))
    else:
        print(format_trace(journal, run_id))
    return 0


def cmd_show(args) -> int:
    journal = Journal(args.db)
    run_id = journal.find_run(args.run)
    event = next((e for e in journal.events(run_id) if e.seq == args.seq), None)
    if event is None:
        print(f"run {run_id} has no event #{args.seq}")
        return 1
    print(f"event #{event.seq} {event.type}  at {event.ts}")
    print(f"run {event.run_id}  agent {event.agent_id}  task {event.task_id}  id {event.event_id}")
    print(json.dumps(event.payload, indent=2, ensure_ascii=False))
    for key, value in event.payload.items():
        if key.endswith("_blob"):
            print(f"\n--- {key} {value} ---")
            print(json.dumps(journal.get_blob(value), indent=2, ensure_ascii=False))
    return 0


def cmd_replay(args) -> int:
    journal = Journal(args.db)
    run_id = journal.find_run(args.run)
    report = replay_run(journal, run_id)
    print(f"replay of run {run_id}")
    print(
        "mode: deterministic re-execution of the CURRENT kernel against the recorded model "
        "responses, human decisions and starting files. No model or backend is called."
    )
    unsupported = any(d.startswith("unsupported schema_version") for d in report.differences)
    verdict = "MATCH" if report.matched else "NOT REPLAYABLE" if unsupported else "DIVERGED"
    print(f"result: {verdict}")
    print(f"  model requests rebuilt identically: {report.identical_requests}/{report.model_calls}")
    print(
        f"  events compared: {report.events_compared}  "
        f"(original ended with {report.original_status}, replay with "
        f"{report.replay_status or '-'})"
    )
    for d in report.differences:
        print(f"  difference: {d}")
    print("not reproduced by design:")
    for n in report.not_reproduced:
        print(f"  - {n}")
    return 0 if report.matched else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-lab", description="BAPE-LAB agent kernel")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db", default=str(DEFAULT_DB), help="journal database (default: runs/lab.db)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_run_options(sp):
        sp.add_argument(
            "--scripted",
            metavar="FILE",
            help="use the scripted fake model (offline) with responses from FILE",
        )
        sp.add_argument(
            "--approve",
            choices=["ask", "yes", "no"],
            default="ask",
            help="answer to 'ask' permissions: prompt (default), always yes, always no",
        )
        sp.add_argument("--runs-dir", default=str(DEFAULT_RUNS), help="where workspaces go")

    r = sub.add_parser("run", parents=[common], help="run a task: agent-lab run tasks/<name>")
    r.add_argument("task", help="task directory containing task.toml")
    add_run_options(r)
    r.set_defaults(func=cmd_run)

    rr = sub.add_parser(
        "rerun", parents=[common], help="a NEW run with a recorded run's task and files"
    )
    rr.add_argument("run", help="run id, or a unique start/end of it")
    add_run_options(rr)
    rr.set_defaults(func=cmd_rerun)

    sub.add_parser("runs", parents=[common], help="list recorded runs").set_defaults(func=cmd_runs)

    t = sub.add_parser("trace", parents=[common], help="human-readable timeline of a run")
    t.add_argument("run", help="run id, or a unique start/end of it")
    t.add_argument("--json", action="store_true", help="print the metrics summary as JSON")
    t.set_defaults(func=cmd_trace)

    s = sub.add_parser(
        "show", parents=[common], help="one event in full, including stored request/response bodies"
    )
    s.add_argument("run")
    s.add_argument("seq", type=int)
    s.set_defaults(func=cmd_show)

    rp = sub.add_parser(
        "replay", parents=[common], help="re-execute a run against its recording (no model call)"
    )
    rp.add_argument("run")
    rp.set_defaults(func=cmd_replay)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LookupError as e:
        print(e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
