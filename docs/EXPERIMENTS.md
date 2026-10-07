# Experiments

> **Status (2026-10-07).** E000: script + offline test ready, not run live (no API key). E001: task, checks, runner ready and tested offline, not run live. E010: **run** (offline), results in `experiments/010-replay-semantics/`. All others: planned.

The lab exists to run experiments. This document defines the method, the metrics, the task bank, and the catalogue.

## 1. Method

Same format as the parent lab (`..\BAPE-LAB\experiments\_template\experiment.md`), extended for agent runs:

```
experiments/NNN-name/
  README.md        Question · Hypothesis (written BEFORE running) · Setup · Procedure ·
                   Evidence (Observed) · Interpretation (Inferred) · Uncertainty (Not yet verified) ·
                   Learned · New Questions
  config/          agent specs, policies, variants (TOML), each referenced by hash
  results/         analysis outputs (tables, plots); raw journals stay in runs/ (git-ignored)
```

Rules:

1. **Hypothesis first.** Written and committed before the first run, including the expected direction and rough size of the effect.
2. **One variable at a time.** Variants differ in exactly the factor under study. Everything else is pinned by hash: specs, policies, task fixtures, model ID, effort, kernel version, memory snapshot.
3. **Repetitions.** Model outputs vary between runs. Each (variant, task) cell runs **N ≥ 5** times to start; report mean and spread, not a single run. Increase N where differences are small.
4. **Matched budgets.** When comparing single- vs multi-agent designs, compare at equal total token budgets as well as at "natural" budgets. Anthropic reports token usage explaining ~80% of performance variance in their research eval; without matching, "multi-agent wins" may just mean "spent more".
5. **Prefer verifiable end states.** Score with deterministic checks (tests pass, exact output match, file exists with property X) wherever possible. Use an LLM judge with a written rubric only where needed, and validate the judge against my own grading on a sample.
6. **Look at transcripts.** Scores say *whether*; transcripts say *why*. Every experiment includes a qualitative read of a few runs per variant.
7. **Budget declared up front.** Each experiment states its maximum spend; the kernel enforces a run-level dollar cap.
8. **Fallback flag.** Runs where the API answered with a fallback model (refusal fallback) are flagged and analysed separately.

## 2. Metrics

| Metric | Definition | Source |
|---|---|---|
| Success | Acceptance checks passed (binary or rubric score 0–1) | Checker / judge |
| Cost | Tokens by type × list price | `model.response` usage |
| Latency | Wall time from `run.started` to `run.completed` | Events |
| Effort | Turns, tool calls, delegations | Events |
| Error rate | Failed tool calls / tool calls | Events |
| Recovery rate | Errors followed by a successful alternative / errors | Events |
| Policy friction | Denials, capability requests, approvals, approval latency | Events |
| Claim accuracy | `complete_task` claims vs verified end state (false success rate) | Checker vs result |
| Context transfer | Brief size; facts needed but missing (judge, against a task-specific fact list) | Briefs + judge |
| Limit hits | Count by type | Events |

## 3. Task bank

A small, local, verifiable set of tasks, grown over time. No web access in early tasks (cost, non-determinism, injection risk).

| Family | Example | Verification |
|---|---|---|
| T1 Find & explain | "Which function in this toy repo mishandles empty input? Cite file:line." | Exact answer key |
| T2 Fix with tests | Toy Python repo with a failing test; fix the bug | Hidden test suite passes (Phase 2, needs sandbox) |
| T3 Extract & transform | Messy CSV/Markdown notes → structured JSON matching a schema | Exact match against expected JSON |
| T4 Document from corpus | Write a summary/report from 10–30 local documents | Rubric judge + fact checklist |
| T5 Multi-part build | Small feature touching several files with a spec | Tests + reviewer rubric |
| T6 Adversarial | Any task above with a planted prompt-injection file | Did the agent follow the injected instruction? Did any effect leave the workspace? |

Start with ~10 tasks across T1, T3, T4 in Phase 1, aiming for ~20 by Phase 4 (Anthropic's evaluation advice: begin with about 20 representative cases rather than waiting for a large suite).

## 4. Catalogue

Status: all *planned*. Hypotheses are filled in by me before each experiment runs.

| # | Name | Question | Design sketch | Needs |
|---|---|---|---|---|
| E000 | Naked loop | What is the minimum code that makes an agent? | Hand-written loop, one tool, raw JSON printed at every step; no kernel | API key |
| E001 | Single-agent baseline | How well does one generalist agent do on the task bank, at what cost? | One spec, all Phase 1 tools, N=5 per task, effort `medium` | Phase 1 |
| E002 | Generalist vs specialists | Do role-specialised agents (narrow prompt and tools) beat one generalist? | Same tasks; generalist vs coordinator + specialists; natural and matched budgets | Phase 2 |
| E003 | Researcher → Builder → Reviewer | Does a fixed pipeline improve T2/T5 success over a single agent? Where do handoffs lose information? | Code-defined pipeline vs single agent; context-transfer metrics at each handoff | Phase 2 |
| E004 | Context transfer | How much does the child need beyond a brief? | brief-only vs brief + summary vs full parent trace (AGENT-MODEL §6) | Phase 2 |
| E005 | Memory | Does memory help on repeated task families, and how often does it hurt? | 5a off vs M2 notes vs M4 memory; 5b agent-written vs curated; 5c planted bad memory | Phase 4 |
| E006 | Restricted permissions | What do agents do when denied? Does restriction reduce damage without killing success? | Same tasks under permissive vs least-privilege policy; classify reactions (adapt, request, give up, workaround attempts) | Phase 1 |
| E007 | Failure injection | How do tool failures propagate and get recovered in single vs multi-agent runs? | Kernel injects timeouts/errors/stale writes at controlled rates (chaos mode); measure recovery and cascade depth | Phase 2 |
| E008 | Human approval | Which policies cause approval fatigue, and does approval catch real problems? | Policies with different `ask` scopes; count approvals, latency, and how many asks were actually risky | Phase 1–3 |
| E009 | Independent review | Do N independent reviewers find more real issues than 1? Do they disagree usefully? | Seeded-bug tasks; 1 vs 3 reviewers vs 3 reviewers + adjudicator; precision/recall of findings | Phase 2 |
| E010 | Replay & fork | Is the kernel deterministic? What changes when one decision changes? | Re-execute recorded runs (must match); fork at key decisions (approval flipped, reviewer stricter) | Phase 1 (replay), Phase 2 (fork) |
| E011 | Orchestration strategies | Static pipeline vs LLM orchestrator vs shared-workspace "blackboard" | Same tasks and budgets; compare success, cost, conflicts | Phase 2–5 |
| E012 | Prompt injection containment | With planted injections, what fraction of runs follow them, and do kernel controls contain the effect? | T6 tasks; capabilities on/off; taint rule on/off (self-test only) | Phase 2+ |
| E013 | Model and effort mix | Does an Opus lead with cheaper workers match all-Opus at lower cost? Which effort level is the sweet spot? | Effort sweep low→high on E001; Opus lead + Sonnet 5.5 / Haiku 4.5 workers | Phase 2 |
| E014 | Tool surface | Bash-in-sandbox vs dedicated tools: success, cost, and observability | Same tasks, two tool surfaces | Phase 2 |
| E015 | Runaway detection | Which loop guards fire in practice, and are they too strict or too loose? | Analyse limit-hit events across all experiments; adversarial loop-inducing tasks | Ongoing |
| E016 | Stated intent | Does requiring an `intent` argument on tools change behaviour or improve diagnosability? | With/without; compare success, tokens, and intent–action consistency | Phase 1+ |
| E017 | Sync vs async delegation | Does non-blocking delegation reduce wall time without hurting quality? | Same orchestrator, two delegation modes | Phase 5 |
| E018 | Claim verification | How often do agents claim success that the end state contradicts? Does a verifier agent reduce it? | False-success rate with and without a verification step | Phase 2 |
| E019 | Progress updates | Are `display: "updates"` notes consistent with actions? Do they help a human predict the next action? | Read-through study on recorded runs | Phase 1 |
| S-01…S-06 | Sandbox experiments | See `SANDBOX.md` §3 | | Phase 2 |

## 5. Order of attack

1. E000 (understand the bare loop).
2. E001 + E010-replay (baseline, and proof the kernel is deterministic).
3. E006, E016, E019 (cheap, single-agent, teach observability and permissions).
4. S-01, then E003/E004/E009 (first real multi-agent questions).
5. The rest as questions arise. New experiments are added from each experiment's "New Questions".
