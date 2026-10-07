# Vision

## Why this lab exists

Most agent tutorials hand you a framework and a working demo. You end up knowing how to call the framework, not what an agent is. This lab goes the other way: build the moving parts from primitives, make every one of them observable, and then run controlled experiments to find out which ideas about agents actually hold up.

The core question:

> What does an LLM-based agent system actually do, step by step, and which design choices (roles, tools, permissions, memory, orchestration) change the outcome?

## What the lab should eventually let me do

1. Define agents with specialised roles, tools, permissions, limits, and memory settings, as versioned configuration.
2. Run them against tasks inside a controlled workspace, with code execution isolated in a sandbox.
3. Let agents delegate to other agents and watch exactly what context crosses each boundary.
4. See every model call, tool call, permission decision, approval, delegation, failure, and recovery as structured events.
5. Replay any run exactly, and fork it at any point into a counterfactual ("what if the reviewer had been stricter here?").
6. Run experiments that compare designs on the same task bank: single vs multi-agent, generalist vs specialist, memory on/off, restricted vs unrestricted tools, different orchestration and review strategies.
7. Eventually, browse all of this visually in an **Agent Observatory**.

## Non-goals

- Not a product, not a platform for other users, not multi-tenant.
- Not a general automation tool for my real computer. Agents work in disposable workspaces, not on my real files.
- Not a framework comparison exercise. Frameworks are studied for ideas, not adopted by default.
- Not a benchmark leaderboard. Experiments answer my questions on my task bank; results do not generalise automatically.

## Principles

1. **Understanding over speed.** Each mechanism comes with an explanation of the concept underneath it.
2. **Complexity needs a reason.** A new component, dependency, or service enters only through a decision record or an experiment that needs it.
3. **Everything is an event.** If it is not in the journal, it did not happen. Observability is designed in, not bolted on.
4. **The model is untrusted.** Model output is treated like input from an unknown user: validated, permission-checked, and sandboxed. This holds whether the cause is a bug, a misunderstanding, or prompt injection.
5. **Explicit over hidden.** Context transfer, permissions, termination, and retries are explicit, inspectable operations, not side effects of a framework.
6. **No fake multi-agent systems.** Every agent role must have a job a single agent could not do as well (parallelism, context isolation, a narrower tool set, a different model, independent judgement). If an experiment shows a role adds nothing, the role goes.
7. **Honest claims.** Every claim about behaviour is labelled *Observed* (seen on this machine), *Inferred*, or *Not yet verified*. Security claims are stated with their limits.
8. **Safe first, capable later.** Start read-only and without code execution. Widen capability one rung at a time, each rung justified by an experiment.

## Research questions

These drive the experiment catalogue in `EXPERIMENTS.md`.

- **R1 Agent anatomy.** What is the minimum machinery that turns a stateless model into an agent? What does each piece contribute?
- **R2 Tools.** How does tool design (granularity, descriptions, bash vs dedicated tools) change behaviour, cost, and observability?
- **R3 Context transfer.** When one agent delegates to another, how much of the needed context survives the brief? Does sharing more (summaries, full traces) help or hurt?
- **R4 Specialisation.** When do specialised agents beat one generalist with all the tools, and at what token cost?
- **R5 Orchestration.** Static code-defined pipelines vs an LLM orchestrator vs a shared blackboard: which is more reliable for which tasks?
- **R6 Review and disagreement.** Do independent reviewers catch more real problems than one reviewer, or do they mostly add noise and cost?
- **R7 Permissions.** How do agents behave when they lack a permission? Do restrictions reduce damage without destroying task success?
- **R8 Failure and recovery.** How do tool failures propagate through a multi-agent run, and which recovery strategies work?
- **R9 Memory.** Does persistent memory improve later runs, and how often does it make things worse (stale or poisoned memory)?
- **R10 Observability.** What can we reliably learn about agent behaviour from actions and stated intents alone, without the chain of thought?
