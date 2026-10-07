"""E001 runner: N live runs of the baseline task, one results row per run.

    uv run python experiments/001-single-agent-baseline/run_e001.py --repeat 5

Costs money. Each run is capped by the task's max_cost_usd; you confirm the total first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anthropic

from agentlab import config
from agentlab.journal import Journal
from agentlab.kernel import deny_all
from agentlab.run import start_run
from agentlab.task import Task
from agentlab.trace import summarize

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks" / "e001-meeting-actions"
RESULTS = Path(__file__).resolve().parent / "results" / "e001-runs.md"
HEADER = (
    "| run | success | turns | tool calls | denied | input tok | output tok | est. $ | seconds "
    "| final output |\n|---|---|---|---|---|---|---|---|---|---|\n"
)


def row(s: dict) -> str:
    final = s["final_output"].replace("\n", " ").replace("|", "/")[:120]
    return (
        f"| {s['run_id'][-8:]} | {s['success']} | {s['turns']} | {s['tool_calls']} | "
        f"{s['tool_denied']} | {s['input_tokens']} | {s['output_tokens']} | "
        f"{s['est_cost_usd']} | {s['duration_s']} | {final} |\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    key = config.api_key()
    if not key:
        print("ANTHROPIC_API_KEY is not set (environment or .env). E001 needs the real API.")
        return 2
    task = Task.load(TASK)
    cap = task.limits.max_cost_usd * args.repeat
    print(f"E001: {args.repeat} run(s) of {task.name} on {task.agent['model']}; "
          f"worst case ${cap:.2f} total (${task.limits.max_cost_usd} per run).")
    if not args.yes and input("continue? [y/N] ").strip().lower() not in ("y", "yes"):
        return 1

    client = anthropic.Anthropic(api_key=key, max_retries=0)
    journal = Journal(config.PROJECT_ROOT / "runs" / "lab.db", secrets=[key])
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS.exists():
        RESULTS.write_text(HEADER, encoding="utf-8")
    for i in range(args.repeat):
        # No human in the loop: the policy has no "ask" rules, and anything unexpected is denied.
        result = start_run(task, journal, client, approver=deny_all,
                           runs_dir=config.PROJECT_ROOT / "runs",
                           extra={"experiment": "E001", "repetition": i + 1})
        s = summarize(journal, result.run_id)
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(row(s))
        print(f"run {i + 1}: {s['status']} success={s['success']} turns={s['turns']} "
              f"${s['est_cost_usd']}  (trace: agent-lab trace {s['run_id'][-8:]})")
    print(RESULTS.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
