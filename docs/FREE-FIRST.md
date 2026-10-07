# Free-first, local-first

Status (2026-10-07): principles **approved**; provider-neutral core **IMPLEMENTED** (MODEL-GATEWAY steps 1-6); local model backend **PROPOSED** (step 7, not started). §3 is the audit of the Phase 1 code as it was *before* the refactor; that coupling has since been removed from the core (see MODEL-GATEWAY.md status). Companion documents: `LOCAL-LLM.md` (runtime and model choice), `MODEL-GATEWAY.md` (the backend-neutral model interface and the implementation plan).

## 1. The rule

The lab must be fully useful with **zero spending on model APIs**. Concretely:

| Path | What it is | Needs | Status |
|---|---|---|---|
| **Tests** | Scripted (fake) model | Nothing: no internet, no key, no local model | IMPLEMENTED, provider-neutral (`backends/scripted.py`); tests prove no SDK, key or network is needed |
| **Real AI, default** | Local model through a local runtime | The runtime (free, open source) and a model file (free to download) | Adapter IMPLEMENTED (`backends/openai_compat.py`, loopback only); NOT VERIFIED with a real server; runtime and model not installed |
| **Real AI, optional** | External API adapter (Anthropic, maybe others) | An API key the owner chooses to pay for | IMPLEMENTED as an optional adapter (`backends/anthropic.py`), off by default: needs the `anthropic` extra, `AGENTLAB_ENABLE_EXTERNAL_API=1` and a key. Not exercised live since the refactor. |

Never required: Anthropic credits, OpenAI credits, paid SaaS, subscriptions, cloud agent services.

What "free" still costs: disk (about 1-3 GB per model), RAM while a model runs, time (local models on this laptop are slow), and electricity. Experiments report those instead of dollars.

## 2. Why this is also better architecture

Phase 1 was built against one provider's wire format. Free-first forces a separation the lab should have anyway:

```
Agent loop ──> ModelGateway ──> ModelBackend (one interface)
                                 ├── ScriptedBackend         tests, demos, replay
                                 ├── LocalBackend            default real model (llama.cpp llama-server)
                                 └── ExternalBackend         optional, off unless configured
                                      └── AnthropicBackend
```

The kernel then learns the *general* architecture of an agent: a model proposes structured tool calls, the kernel decides. Which company trained the model, and how its API spells "tool call", becomes an adapter detail. That is the thing worth understanding.

## 3. Audit of the Phase 1 code (Observed, 2026-10-07)

The kernel's *ideas* are provider-neutral: loop, policy, approvals, journal, budgets, workspace. Its *data shapes* are not. Anthropic coupling, from shallow to deep:

### 3.1 Import coupling (easy to remove)

| Where | What |
|---|---|
| `run.py:12, 81` | `import anthropic` only to record the SDK version |
| `gateway.py:27, 41-42, 103` | Retry classification uses `anthropic.APIError`, `APIConnectionError`, status codes |
| `scripted.py:16`, `replay.py:29` | Responses are built as `anthropic.types.Message` objects |
| `cli.py:61-69`, `config.py` | The only live path builds `anthropic.Anthropic(...)`; "no API key" means "no real model" |
| `pyproject.toml` | `anthropic` is the single runtime dependency |

### 3.2 Wire-format coupling (the real work)

| Where | Anthropic-specific shape |
|---|---|
| `agent.py:63-72` | Request = `{model, max_tokens, system, tools, messages, output_config}`; system prompt as top-level field; `output_config.effort` |
| `agent.py:78-93` | Reads `stop_reason` values `tool_use` / `end_turn`; finds calls as content blocks with `type == "tool_use"`; appends the raw assistant `content` list |
| `kernel.py:44, 75, 178` | Consumes `{"id", "name", "input"}` tool_use blocks; returns `{"type": "tool_result", "tool_use_id", "content", "is_error"}` blocks |
| `tools.py:32-37` | Tool definition = `{name, description, input_schema, strict}` (Anthropic's spelling; OpenAI-style APIs use `{"type": "function", "function": {name, description, parameters}}`) |
| `gateway.py:113-139` | Response read via `Message.to_dict()`; payload fields `stop_reason`, `content_types`, `tool_uses`, `request_id` from `_request_id` |
| `budget.py:9-14, 43-53` | Usage keys `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`; Anthropic price table; spend cap is the main runaway guard |
| `trace.py:30, 38` | Displays `output_config.effort`, `stop_reason`, `input_tokens` |
| `replay.py` | Compares Anthropic-shaped request bodies; serves Anthropic `Message`s |
| `tasks/*/task.toml`, `scripted.json`, all tests | Anthropic model ids, effort, Anthropic-shaped scripted responses |

### 3.3 Semantic coupling (keep the idea, generalise the reason)

| Phase 1 mechanism | Anthropic reason | Does it survive locally? |
|---|---|---|
| Append-only transcript, prefix check | Opus 5.5 rejects edited histories; prompt caching | **Yes, and it matters more.** `llama-server` reuses its KV cache for an unchanged prefix (`cache_prompt`, default true; verified in the server README). On a CPU, re-reading a long prompt is the slowest part of a turn. |
| Requested vs actual model, mismatch fails | Server-side fallback could switch models | Yes. For local runs "actual model" = the GGUF file the server loaded (path + SHA-256 from our config, model info from `/props`). |
| Thinking blocks passed back unchanged | Signatures bind thinking to the conversation | Provider-specific. Becomes an opaque per-provider payload the Anthropic adapter re-emits; other adapters ignore it. |
| Spend cap (`max_cost_usd`) | Every token costs money | Local tokens cost $0. Runaway guard becomes **wall-clock time** and token counts. |
| `effort` | Opus 5.5 thinking depth control | Provider option. Local equivalent knobs: thinking on/off (`chat_template_kwargs.enable_thinking` on Qwen3), `--reasoning-budget`, temperature. |
| Refusal stop reason | Safety classifiers | Local models refuse in plain text, not via a stop reason. Normalised `finish_reason: "refusal"` only where a backend reports it. |

### 3.4 What is already provider-neutral

`policy.py`, `workspace.py`, the journal storage (`journal.py`: event envelope, blobs, append-only triggers), task checks (`task.py:evaluate_checks`), the CLI structure, the experiment format. These stay as they are.

## 4. Principles (additions to VISION.md)

1. **The kernel never imports a provider SDK.** Only adapters do.
2. **Every adapter is optional except Scripted.** The test suite runs with no network, no key, no local model.
3. **External APIs are off unless explicitly configured**: an extra package install, an explicit backend name in the task file, an environment flag, and a key. Missing any one: the run refuses to start with a clear message.
4. **The kernel stays authoritative.** A local model, like any model, only *proposes* tool calls. Nothing in the runtime executes tools.
5. **Measure local cost honestly**: seconds per turn, tokens per second, peak RAM, prompt tokens re-read vs served from cache.
6. **Do not claim hardware compatibility until an experiment has observed it.**

## 5. Labels used in the free-first documents

- **IMPLEMENTED**: exists in the code on `main` and is tested.
- **PROPOSED**: designed here, not built.
- **EXPERIMENTAL**: will be built, but only as an experiment arm; may be removed.
- **NOT VERIFIED**: a claim from documentation, papers or arithmetic that has not been observed on this machine.
