# Experiments

> **Status (2026-10-07).** E000: script + offline test ready, not run live (no API key). E001: task, checks, runner ready and tested offline, not run live. E010: **run** (offline), results in `experiments/010-replay-semantics/`. All others: planned. **Free-first revision (2026-10-07, PROPOSED):** live runs will use a local model, never a paid API; E000/E001 live arms move to the local backend once built; E020-E024 are defined in section 5.

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


---

## 5. Local-model experiments (PROPOSED, 2026-10-07)

Free-first revision (`FREE-FIRST.md`, `LOCAL-LLM.md`, `MODEL-GATEWAY.md`). None of these can run before implementation steps 1-9 and the owner's manual install of `llama-server` + Qwen3-1.7B. Cost: $0; time and RAM only. The hypotheses below are Claude's, written before any local model exists on the machine; the owner may add their own before running.

Shared setup unless stated: `llama-server` CPU x64 build pinned by number, `--jinja -c 4096 -np 1 --host 127.0.0.1`; Qwen3-1.7B Q4_K_M pinned by SHA-256; temperature 0, seed 1, thinking off; other apps closed; laptop on mains power. Every run records build, model hash, server `/props`, and free RAM at start.

Conclusion format for all five: a table of measurements, then labelled paragraphs **Observed** (numbers and transcripts), **Inferred** (what they suggest), **Not yet verified** (what would change the conclusion), then **New questions**.

### E020 — Local model baseline

- **Question.** Can this laptop serve Qwen3-1.7B fast enough and within memory for an agent loop?
- **Hypothesis.** Loads and serves. Peak working set of `llama-server` 1.6-2.2 GB at 4k context. Generation 8-15 tok/s (below the ~23 tok/s single-channel bandwidth bound). First-turn prompt processing (~1-2k tokens) takes tens of seconds; later turns are much faster because the prefix is cached (`timings.cache_n` close to the previous prompt length). The Vulkan build speeds prompt processing more than generation. Qwen3-4B Q4_K_M works only with nearly everything closed, at under half the 1.7B generation speed.
- **Setup.** Arms: (a) 1.7B Q4_K_M CPU, (b) 1.7B Q4_K_M Vulkan, (c) 1.7B official Q8_0 CPU, (d) 4B Q4_K_M CPU (stretch; skipped if free RAM < 3.5 GB). Fixed prompt set: a short chat, a ~1.5k-token tool-schema-heavy prompt, a 3-turn conversation that grows by appending.
- **Procedure.** Start the server per arm; `agent-lab local check`; send each prompt 5 times through the gateway (no tools executed); record `timings` and process memory (`Get-Process llama-server`).
- **Measurements.** Load time; peak working set; prompt tokens/s; generated tokens/s; `cache_n` on appended turns; free system RAM; paging stalls.
- **Expected observations.** As hypothesised; appended-turn prompt time drops sharply with cache hits.
- **Failure conditions.** Server fails to start or is killed (out of memory); a single short turn takes > 120 s; Windows becomes unusable while serving. If arm (a) fails: fall back to Qwen3-0.6B for plumbing only, and record that "useful local agent" is not met on this hardware.

### E021 — Local tool calling

- **Question.** Does Qwen3-1.7B, served by `llama-server --jinja`, produce structured tool calls the kernel can execute, and how does the chat template present tools to the model?
- **Hypothesis.** `/apply-template` shows the tools rendered as a Hermes-style block in the system turn; replies come back as `tool_calls` with string `arguments` and ids. On single-step requests ("read notes/meeting.md") at least 90% of calls name the right tool with schema-valid arguments.
- **Setup.** `demo-permissions` workspace; three tools; 20 single-step prompts (5 per tool + 5 that need no tool).
- **Procedure.** Record `/props` (does the server report tool-call support for this template?) and one `/apply-template` output. Run the 20 prompts x 3 repetitions through the full kernel (policy on, approvals auto-deny).
- **Measurements.** Right tool chosen; arguments parse; arguments schema-valid; unwanted calls on no-tool prompts; ids present; latency per call.
- **Expected observations.** High validity on single steps; occasional extra or missing calls on no-tool prompts.
- **Failure conditions.** < 70% valid calls, or calls appear as text in `content` instead of `tool_calls` → native strategy not viable for this model; switch the default to `constrained_json` and record why.

### E022 — Local model failure modes

- **Question.** How does a small local agent fail on multi-step tasks, and does the kernel contain every failure?
- **Hypothesis.** On `e001-meeting-actions` (needs ~4-6 calls) success is at most 40% over 10 runs. Dominant failures, in order: stopping early with a confident wrong answer; repeating the same call; malformed or invented arguments (wrong paths); attempted writes outside `out/` (denied); hitting `max_turns`. No failure escapes the workspace or bypasses policy.
- **Setup.** E001 task on the local backend, native strategy; `max_turns` 12, `max_wall_seconds` 900.
- **Procedure.** 10 runs. Read every transcript; label each failure with a fixed code list (early stop, repetition, invalid args, wrong tool, permission denied, invented file, limit hit, other) and map to the MAST categories where they fit.
- **Measurements.** Success rate; failure codes per run; turns and wall time; denials; repeated identical calls; claim vs reality (final text vs checks).
- **Expected observations.** Failures concentrated in multi-turn bookkeeping rather than single calls (consistent with the reported low multi-turn BFCL score of small models).
- **Failure conditions (of the experiment).** Too slow to finish 10 runs in a session → reduce to 5 and say so. Kernel crash or any effect outside the workspace → stop and fix as a kernel bug.

### E023 — Local vs scripted agent

- **Question.** What differs between a scripted run and a real local run of the same task through the same kernel, and how reproducible is the local model?
- **Hypothesis.** Event types follow the same grammar in both; content differs. With temperature 0 and a fixed seed, 5 repeated local runs give identical tool-call sequences in most but not all runs (possible CPU threading/batching nondeterminism). Replay of every local run matches exactly (replay never calls the model).
- **Setup.** `demo-permissions` with its script vs the local model; same kernel commit.
- **Procedure.** 5 local runs; compare tool-call sequences across runs; `agent-lab replay` each; diff the event-type sequence against the scripted run.
- **Measurements.** Identical-sequence rate across local reruns; first divergence point; replay MATCH rate; time per run.
- **Expected observations.** Replay 5/5 MATCH; reruns mostly identical.
- **Failure conditions.** A local run's replay diverges → a determinism bug in the kernel or adapter (e.g. unstable key order in the wire body); fix before more local experiments.

### E024 — Structured tool-call reliability

- **Question.** Which strategy gives a small local model the most reliable executable tool calls: native template tool calling or grammar-constrained JSON actions?
- **Hypothesis.** Constrained JSON gives ~100% parseable, schema-valid calls (the grammar forbids anything else) but picks the right tool and arguments less often than native calling on multi-step tasks, because it is not the format the model was trained on. Native calling has a small rate of malformed arguments, caught by the kernel as `invalid_input`.
- **Setup.** Strategies `native` and `constrained_json` (EXPERIMENTAL); optionally a naive free-text arm. Task sets: the 20 single-step prompts from E021 and the E001 task.
- **Procedure.** Each strategy x each prompt x 3 repetitions; E001 x 5 runs per strategy.
- **Measurements.** Parse failures; schema violations; wrong-tool rate; argument correctness (path exists, sensible content); E001 success; tokens and seconds per turn (grammar overhead).
- **Expected observations.** Constrained: zero parse failures, slightly lower task success. Native: rare parse failures, higher task success.
- **Failure conditions.** Grammar generation unsupported or crashing for the schema used (e.g. `oneOf` across tools) → simplify to one generic action schema and record the limitation.
