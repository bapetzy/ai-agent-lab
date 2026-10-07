"""E001 runner: N runs of the baseline task, one results row per run.

    uv run python experiments/001-single-agent-baseline/run_e001.py --repeat 5

Free-first: the task's backend is the LOCAL model (openai_compat / llama-server), which is
not implemented yet (docs/MODEL-GATEWAY.md step 7). Until then this runner explains that and
exits. It never falls back to a paid API.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agentlab import config
from agentlab.backends import BackendNotAvailable, make_backend, secrets_of
from agentlab.journal import Journal
from agentlab.kernel import deny_all
from agentlab.run import start_run
from agentlab.task import Task
from agentlab.trace import summarize

ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / "tasks" / "e001-meeting-actions"
RESULTS = Path(__file__).resolve().parent / "results" / "e001-runs.md"
HEADER = (
    "| run | backend | model | success | turns | tool calls | malformed | denied | input tok "
    "| output tok | seconds | final output |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def row(s: dict) -> str:
    final = s["final_output"].replace("\n", " ").replace("|", "/")[:120]
    return (
        f"| {s['run_id'][-8:]} | {s['backend']} | {s['model_requested']} | {s['success']} | "
        f"{s['turns']} | {s['tool_calls']} | {s['tool_malformed']} | {s['tool_denied']} | "
        f"{s['input_tokens']} | {s['output_tokens']} | {s['duration_s']} | {final} |\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11))
    args = parser.parse_args()

    task = Task.load(TASK)
    try:
        backend = make_backend(task)
    except BackendNotAvailable as e:
        print(f"E001 cannot run yet: {e}")
        return 2

    journal = Journal(config.PROJECT_ROOT / "runs" / "lab.db", secrets=secrets_of(backend))
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS.exists():
        RESULTS.write_text(HEADER, encoding="utf-8")
    for i in range(args.repeat):
        # No human in the loop: the policy has no "ask" rules; anything unexpected is denied.
        result = start_run(
            task,
            journal,
            backend,
            approver=deny_all,
            runs_dir=config.PROJECT_ROOT / "runs",
            extra={"experiment": "E001", "repetition": i + 1},
        )
        s = summarize(journal, result.run_id)
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(row(s))
        print(
            f"run {i + 1}: {s['status']} success={s['success']} turns={s['turns']} "
            f"{s['duration_s']}s  (trace: agent-lab trace {s['run_id'][-8:]})"
        )
    print(RESULTS.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
