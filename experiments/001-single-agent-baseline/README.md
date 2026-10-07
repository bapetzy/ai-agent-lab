# E001 — Single-agent baseline

**Status:** structure ready, task and runner tested offline. **Not yet run live** (no API key as of 2026-10-07).

This is the control condition for later experiments (specialists, pipelines, memory, restricted permissions). Do not optimise it. Its value is being simple and stable.

## Question

How well does one generalist agent with three file tools and a least-privilege policy do on a small, verifiable task, and what does it cost?

## Hypothesis (fill in before the first live run)

- Success rate over N runs: ___
- Typical turns / tool calls: ___
- Typical cost per run: ___
- Most likely failure: ___ (my guess: including the cancelled catering item, or missing the *reopened* backup item)

## Setup (pinned)

| Item | Value |
|---|---|
| Task | `tasks/e001-meeting-actions` (two meeting notes; 4 open items, 1 done, 1 cancelled, 1 done-then-reopened) |
| Model | `claude-opus-5-5`, effort `medium`, `max_tokens` 8000 per response, fallback disabled |
| Tools | `list_files`, `read_file`, `write_file` |
| Permissions | read anything in the workspace; write only `out/**`; everything else denied |
| Limits | 12 turns, 30 tool calls, 200k input / 40k output tokens, **$0.50 per run** |
| Success | all 8 deterministic checks in `task.toml` pass (owners present, done/cancelled items absent, final answer says 4) |

Every run records task hash, model requested and used, tools, policy, token usage, turns, tool calls, duration, success and final output (`agent-lab trace <run> --json`).

## Procedure

```powershell
# one run, inspect it
uv run agent-lab run tasks/e001-meeting-actions
# N runs + results table (asks for confirmation; total spend <= N x $0.50)
uv run python experiments/001-single-agent-baseline/run_e001.py --repeat 5
```

`run_e001.py` appends one row per run to `results/e001-runs.md` and prints the table.

## Evidence (Observed)

No live runs yet.

| run | success | turns | tool calls | denied | input tok | output tok | est. $ | seconds | final output |
|---|---|---|---|---|---|---|---|---|---|

## Interpretation / Uncertainty / New Questions

After the first runs. Known limitation of the checks: `final_text_contains "4"` fails if the agent writes "four".
