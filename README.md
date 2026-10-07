# BAPE-LAB — AI Agent Laboratory

A personal laboratory for finding out how far an LLM-based multi-agent system can be pushed when it has tools, roles, permissions, memory, orchestration, observability, and a controlled execution environment.

This is not a product. The product is understanding: what an agent actually is, how it uses tools, how context moves between agents, where multi-agent systems help and where they hurt, and how to observe and evaluate all of it without relying on hidden reasoning.

This project is the "AI that can use my computer" rabbit hole (Stages 7–8) of the parent lab at `..\BAPE-LAB`, which it inherits its working rules from: hypothesis before experiment, label every claim **Observed / Inferred / Not yet verified**, and treat "new questions" as the most important output.

## Status

- **Phase 0** (research and architecture): approved 2026-10-07.
- **Phase 1** (walking skeleton: one agent, fully observed): implemented 2026-10-07, awaiting review. No multi-agent, no code execution, no UI.
- Live API runs have **not** happened yet: no API key on this machine. Everything below works offline with a scripted (fake) model.
- **Free-first revision (PROPOSED, 2026-10-07):** the lab must run with zero paid API usage. Default real model becomes a local model (llama.cpp `llama-server` + Qwen3-1.7B); the Anthropic adapter becomes optional and off by default. See [docs/FREE-FIRST.md](docs/FREE-FIRST.md), [docs/LOCAL-LLM.md](docs/LOCAL-LLM.md), [docs/MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md). The live commands below that need `ANTHROPIC_API_KEY` will be replaced.

Docs mark each part as **IMPLEMENTED**, **PROPOSED** (designed, not built) or **NOT YET VERIFIED**.

## Quick start (PowerShell)

```powershell
uv sync                                   # install (Python 3.14 via uv)
uv run pytest                             # 84 tests, offline, no API key needed

# Offline demo of every kernel path (allow, deny, ask a human, tool failure), fake model:
uv run agent-lab run tasks/demo-permissions --scripted tasks/demo-permissions/scripted.json
uv run agent-lab runs                     # list runs (short id = last 8 characters)
uv run agent-lab trace <id>               # readable timeline of one run
uv run agent-lab trace <id> --json        # the run's metrics
uv run agent-lab show <id> 2              # one event in full, incl. the exact request sent
uv run agent-lab replay <id>              # re-execute the kernel against the recording

# Live (costs money): put ANTHROPIC_API_KEY in .env (copy .env.example), then
uv run agent-lab run tasks/e001-meeting-actions
uv run agent-lab rerun <id>               # a NEW live run with the same task and files
```

`--approve ask|yes|no` controls what happens when the policy says "ask a human" (default: prompt in the terminal; denied when there is no terminal).

## What happens in one run

```
task.toml + workspace/  ->  run_started (config, policy, starting files recorded)
loop:
   -> model_request   (whole conversation; stored as a blob)
   <- model_response  (stop reason, usage, requested vs actual model)
   for each tool_use the model asked for:
       tool_requested -> permission_checked -> [approval_requested -> approval_resolved]
       -> permission_approved | permission_denied
       -> tool_started -> tool_completed | tool_failed
   all tool results go back to the model in one message
until the model stops asking for tools (or a limit / error stops the run)
check_result (deterministic checks decide success) -> run_completed | run_failed
```

## Read the code in this order

| File | What it shows |
|---|---|
| `experiments/000-bare-agent-loop/bare_loop.py` | The agent loop with nothing else: about 60 lines against the raw API |
| `src/agentlab/agent.py` | The same loop inside the lab: budget check, gateway, kernel |
| `src/agentlab/kernel.py` | One tool call: validate, policy, human approval, execute, journal |
| `src/agentlab/policy.py` | Capabilities and first-match rules, default deny |
| `src/agentlab/workspace.py` | Path confinement (and its limits) |
| `src/agentlab/tools.py` | The three tools: list, read, write |
| `src/agentlab/gateway.py` | The only code that talks to the model; prefix check, retries, model check |
| `src/agentlab/journal.py` | Append-only SQLite events and blobs |
| `src/agentlab/budget.py` | Limits and the cost estimate |
| `src/agentlab/replay.py` | What replay does and does not reproduce |
| `src/agentlab/run.py`, `task.py`, `trace.py`, `cli.py` | Glue: one run, task files, readable output, commands |

## Security, honestly

The Phase 1 permission policy and the per-run workspace are **application-level mechanisms, not a sandbox**. They control what an agent can do *through the kernel's three file tools*: it cannot read or write outside its disposable workspace copy, cannot write where the policy forbids, and has no shell, subprocess, or network tool. They would not stop code running with OS access (there is none in Phase 1), and their strength is exactly the correctness of `workspace.py`, `policy.py` and `kernel.py`. Workspace contents and tool outputs are sent to Anthropic's API. Details: `docs/SANDBOX.md`.

## The idea in one paragraph

The recommended architecture is an **agent kernel**: a small Python program that plays the role an operating system kernel plays for processes. Agents are like processes, with their own context and an append-only transcript. Tool calls are like system calls: an agent cannot touch anything directly, it can only ask the kernel, and the kernel validates the request, checks it against the agent's capabilities, asks a human when policy says so, executes it (inside a sandbox when it runs code), and writes everything to an append-only journal. Because every non-deterministic input (model responses, tool results, human decisions, time) enters through that journal, any run can be inspected, re-executed deterministically, or forked into a counterfactual. Multi-agent behaviour is built on the same mechanism: delegation is a kernel call that creates a task and a fresh agent with an explicit brief, so context transfer becomes something we can measure.

## Map

| Document | What it answers |
|---|---|
| [docs/VISION.md](docs/VISION.md) | Goals, non-goals, principles, the research questions |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Environment reconnaissance, landscape survey, verified sources, what remains unverified |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Recommended stack, alternatives, the kernel design, backend/frontend/storage/testing/workflow, security assumptions, limitations |
| [docs/AGENT-MODEL.md](docs/AGENT-MODEL.md) | What an agent is; agent specs and instances; tasks; delegation; context transfer; loops; failures and retries |
| [docs/TOOL-MODEL.md](docs/TOOL-MODEL.md) | How tool calls look on the wire; tool specs; effect classes; capabilities and permissions; human approval |
| [docs/SANDBOX.md](docs/SANDBOX.md) | Threat model; the isolation ladder and what each rung does *not* guarantee; the plan for this machine |
| [docs/MEMORY.md](docs/MEMORY.md) | Context vs memory; memory tiers; compaction under append-only transcripts; memory risks |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Event model, journal, causality, replay and fork, inspecting behaviour without chain-of-thought |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Experiment method, metrics, the task bank, the experiment catalogue |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log |
| [docs/FREE-FIRST.md](docs/FREE-FIRST.md) | Zero-paid-API rule; audit of Anthropic coupling in Phase 1 (PROPOSED revision) |
| [docs/LOCAL-LLM.md](docs/LOCAL-LLM.md) | Machine constraints, local runtime and model decision (PROPOSED) |
| [docs/MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md) | Backend-neutral model interface, tool-calling strategy, implementation plan (PROPOSED) |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phases, Phase 1 status checklist; what not to build yet |
| [experiments/](experiments/) | E000 bare loop, E001 single-agent baseline, E010 replay semantics |

Suggested reading order: VISION → ARCHITECTURE → AGENT-MODEL → TOOL-MODEL → OBSERVABILITY → SANDBOX → MEMORY → EXPERIMENTS → ROADMAP. RESEARCH and DECISIONS are reference material.
