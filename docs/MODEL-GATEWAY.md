# Model gateway: one interface, many backends

Status (2026-10-07): **steps 1-7 IMPLEMENTED** (provider-neutral core + OpenAI-compatible local backend; owner-approved). Step 7 is tested only against an in-process stub server: **NOT VERIFIED against a real llama-server** (nothing installed). Steps 8-10 (rest of the Anthropic extra work, `local check`, install, E020+) **PROPOSED, not started**. Before the refactor the gateway called the Anthropic SDK directly (`FREE-FIRST.md` §3 describes that state).

Implemented as specified, with these differences:

- `ModelResponse` also has `request_id` (provider request id, journaled).
- The ScriptedBackend lives in `src/agentlab/backends/scripted.py`; the old Anthropic-shaped `src/agentlab/scripted.py` is gone.
- Part of step 8 was needed to meet the approved goal "core must not depend on the Anthropic SDK": `anthropic` is now an optional extra (`[project.optional-dependencies] anthropic`), the adapter lives in `backends/anthropic.py` with a lazy SDK import, and `make_backend` refuses it unless `AGENTLAB_ENABLE_EXTERNAL_API=1`, the extra and a key are present. Its mapping is unit-tested without the SDK; it has not been exercised against the real API since the refactor (no key; never required).
- Step 7 (`backends/openai_compat.py`) follows §4.1, with these decisions (D-033):
  - **Loopback only, no override.** `localhost`, `127.0.0.0/8`, `::1`; anything else (LAN IPs, `0.0.0.0`, hostnames, URLs with credentials, non-http schemes) is refused when the backend is built. The `allow_remote` escape hatch in §7 is **not** implemented: the owner asked for local-only.
  - **Proxies ignored.** The HTTP opener has an empty proxy table, so environment or registry proxy settings cannot route "local" traffic elsewhere (tested with a bogus `HTTP_PROXY`).
  - **Errors.** Connection refused/unreachable → `unavailable` (fails at once, message says how to start `llama-server`). HTTP 408/409/429/5xx (503 = server still loading the model) → `transient` (retried). Other 4xx and non-JSON replies → `permanent`. A request that exceeds `request_timeout_s` → `permanent`, **not retried** (on a CPU model a retry would just wait again).
  - **Finish reasons.** `stop` with tool calls present is normalised to `tool_calls` (some servers do this); `length` stays `length` even with tool calls, so cut-off calls are never executed; `content_filter` → `refusal`; anything else → `other`.
  - **Tool calls.** Arguments as a JSON string are parsed with `ToolCall.from_raw`; an object is accepted too. A missing call id gets a deterministic `call_<n>_<i>`. Assistant turns are sent back with each call's `raw_arguments` verbatim (malformed ones included). Error results are prefixed `ERROR: ` because the format has no error flag.
  - **Options.** Only `enable_thinking` (→ `chat_template_kwargs`) is supported; any other option is refused instead of silently dropped.
  - **Usage.** With llama-server `timings`: `input_tokens = prompt_n`, `cached_input_tokens = cache_n`; otherwise `usage.prompt_tokens` (and `prompt_tokens_details.cached_tokens` if present). `timings` is kept whole on the response. These field meanings come from the llama.cpp server README; NOT VERIFIED on a real server.
  - **describe()** does a best-effort `GET /props` (5 s timeout) and records `model_path`, `build_info`, `total_slots`, `n_ctx` and a SHA-256 of the chat template, or `props_error`.
  - **Model check.** The gateway compares the response's `model` field with `[model].name`. Start llama-server with `--alias <[model].name>` (LOCAL-LLM §6). Whether llama-server echoes the alias there is NOT VERIFIED; if it does not, the run fails with `model_mismatch` rather than continuing.
  - `tool_strategy = "constrained_json"` is refused with a clear message (E024 arm, not built). `model_sha256` is not read yet (belongs to step 9, `local check`).
- Task files use `[model] backend / name / temperature / seed / options`, plus `base_url` and `tool_strategy` for `openai_compat`.

## 1. Goal

```
                 internal types only                    provider wire formats
agent loop ──> ModelGateway ──> ModelBackend.complete(ModelRequest) -> ModelResponse
   ^              │  prefix check, journal,             ├── ScriptedBackend      (default for tests)
   │              │  retries, model check, limits       ├── OpenAICompatBackend  (default real model:
   │              v                                     │      llama-server; also Ollama/LM Studio)
 kernel <── ToolCall / ToolResult                       └── AnthropicBackend     (optional extra, off)
```

The agent loop and the kernel only ever see `ModelRequest`, `ModelResponse`, `ToolCall`, `ToolResult`. They never import a provider SDK and never look at a provider's JSON.

## 2. Internal types

Plain frozen dataclasses in one small module (`src/agentlab/model_types.py`, PROPOSED). Stdlib only.

```python
@dataclass(frozen=True)
class ToolSpec:            # what the model is told about a tool
    name: str
    description: str
    parameters: dict       # JSON Schema (object, required, additionalProperties: false)

@dataclass(frozen=True)
class ToolCall:            # what the model asked for, normalised
    id: str                # provider id, or generated ("call_<turn>_<n>") when the provider has none
    name: str
    arguments: dict | None # parsed arguments; None if they could not be parsed
    raw_arguments: str     # exactly what the model produced (JSON text), for the journal
    parse_error: str | None

@dataclass(frozen=True)
class ToolResult:          # what the kernel answers
    call_id: str
    name: str
    content: str
    is_error: bool

@dataclass(frozen=True)
class Message:
    role: str                          # "user" | "assistant" | "tool_results"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    reasoning: str | None = None       # local models only: returned reasoning text (model output)
    provider_payload: dict | None = None  # opaque; only the backend that produced it reads it

@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int
    temperature: float | None = None
    seed: int | None = None
    options: dict = field(default_factory=dict)   # backend-specific: effort, enable_thinking, ...

@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0       # Anthropic cache reads; llama-server timings.cache_n

@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str     # "tool_calls" | "stop" | "length" | "refusal" | "other"
    usage: Usage
    model: str             # what the backend says actually answered
    reasoning: str | None
    timings: dict | None   # e.g. llama-server timings (prompt_ms, predicted_per_second, cache_n)
    raw: dict              # the provider's response, verbatim, for the journal
    provider_payload: dict | None  # e.g. Anthropic content blocks incl. thinking signatures

class ModelError(Exception):
    kind: str              # "transient" (retry) | "permanent" | "unavailable" (backend not running)
    status: int | None
    request_id: str | None

class ModelBackend(Protocol):
    name: str              # "scripted" | "openai_compat" | "anthropic"
    def complete(self, request: ModelRequest) -> ModelResponse: ...
    def describe(self) -> dict: ...   # facts recorded in run_started (server props, build, model file hash)
```

Design notes:

- **`ToolCall.arguments` may be `None`.** Small local models produce malformed JSON. The gateway does not hide that: the call is journaled with `raw_arguments` and `parse_error`, and the kernel answers it with an `invalid_input` error result. Malformed calls become a *measurable* failure (E024), not a crash.
- **`provider_payload`** is how provider-specific state survives a round trip without leaking into the kernel. Anthropic needs its thinking blocks (with signatures) sent back unchanged; the Anthropic adapter puts the original content blocks there and re-emits them for assistant turns. Other adapters ignore it.
- **`reasoning`**: Anthropic never returns raw reasoning; `llama-server` with a thinking model returns it as `reasoning_content`. When present it is journaled and labelled *model-reported*. It is never fed to policy decisions.
- **`finish_reason`** is a small closed set. The provider's own value is kept in `raw` for the journal.

## 3. The agent loop after the change

Same shape as today (`agent.py`), with neutral types:

```python
messages = [Message("user", text=prompt)]
while True:
    if (why := budget.check_before_model_call()) is not None: return stop(why)
    response = gateway.call(ModelRequest(model, system, tuple(messages), tool_specs, ...))
    budget.record(response.usage, response.timings)
    messages.append(Message("assistant", text=response.text, tool_calls=response.tool_calls,
                            reasoning=response.reasoning, provider_payload=response.provider_payload))
    if response.tool_calls:
        results = tuple(kernel.handle(call) for call in response.tool_calls)  # policy, approval, execution
        messages.append(Message("tool_results", tool_results=results))
        continue
    if response.finish_reason == "stop": return completed(response.text)
    return failed(f"finish_reason:{response.finish_reason}")
```

The kernel's `handle(ToolCall) -> ToolResult` keeps every Phase 1 step (validate, canonical path, policy, approval, execute, journal). Only its input and output types change.

## 4. Wire mappings

### 4.1 OpenAI-compatible chat (llama-server, Ollama `/v1`, LM Studio)

| Internal | Request JSON |
|---|---|
| `system` | first message `{"role": "system", "content": system}` |
| `Message("user")` | `{"role": "user", "content": text}` |
| `Message("assistant")` | `{"role": "assistant", "content": text or null, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": raw_arguments}}]}` |
| `Message("tool_results")` | one `{"role": "tool", "tool_call_id": id, "content": content}` per result (an error result is prefixed `ERROR:`: the format has no `is_error` flag) |
| `ToolSpec` | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| `max_output_tokens`, `temperature`, `seed` | `max_tokens`, `temperature`, `seed` |
| `options.enable_thinking` | `chat_template_kwargs: {"enable_thinking": …}` (llama-server) |
| strategy B (constrained action) | no `tools`; `response_format: {"type": "json_schema", "schema": …}` |

| Response JSON | Internal |
|---|---|
| `choices[0].message.content` | `text` |
| `choices[0].message.reasoning_content` | `reasoning` |
| `choices[0].message.tool_calls[].{id, function.name, function.arguments}` | `ToolCall` (arguments parsed with `json.loads`; failure → `parse_error`) |
| `choices[0].finish_reason` `tool_calls` / `stop` / `length` | `tool_calls` / `stop` / `length` |
| `usage.prompt_tokens`, `usage.completion_tokens`; `timings.cache_n` | `Usage` |
| `model`, `timings` | `model`, `timings` |

The OpenAI-compatible request shape is a de-facto convention, not a standard: details differ per server (whether tool-call ids are returned, whether `arguments` is a string). The adapter normalises; each server is NOT VERIFIED until an experiment talks to it.

### 4.2 Anthropic Messages API (optional adapter; today's code moves here)

| Internal | Anthropic |
|---|---|
| `system` | top-level `system` |
| `ToolSpec` | `{"name", "description", "input_schema", "strict": true}` |
| assistant `Message` | `provider_payload["content"]` re-emitted verbatim if present (thinking blocks); else built from text + `tool_use` blocks |
| `Message("tool_results")` | one user message of `tool_result` blocks with `is_error` |
| `options.effort` | `output_config.effort` |
| `stop_reason` `tool_use` / `end_turn` / `max_tokens` / `refusal` | `tool_calls` / `stop` / `length` / `refusal` |
| `usage.input_tokens`, `cache_read_input_tokens`, … | `Usage` |

### 4.3 Scripted

Scripts become provider-neutral JSON lists of `ModelResponse`-like objects:

```json
{"text": "Let me look.", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "notes/a.md"}}],
 "finish_reason": "tool_calls", "usage": {"input_tokens": 0, "output_tokens": 0}, "model": "scripted-fake"}
```

A script may also contain `"raw_arguments": "{bad json"` to simulate a malformed call (for E024 tests).

## 5. What the gateway does (provider-independent)

1. **Prefix check** on the normalised request: `system`, `tools`, `model` unchanged; earlier messages unchanged. Still motivated by Anthropic's rules, now also by llama-server's prompt cache (append-only = cache hits = faster local turns).
2. **Journal** `model_request` with the normalised request (blob) *and* the exact wire body the backend sent (blob), and `model_response` with normalised fields, `timings`, `reasoning` (if any), the raw response (blob).
3. **Retries** for `ModelError.kind == "transient"`; `unavailable` (local server not running) fails fast with a message telling the owner how to start it.
4. **Model check**: `response.model` must match what the task requested. For local runs the check is stronger: `run_started` records the model file path and SHA-256 from configuration and the server's `/props`; a mismatch refuses to start the run.
5. **No fallback**, for any backend (D-018 stays).

## 6. Journal, replay, budgets

- **Event names stay.** `model_response` gains `backend`, `finish_reason`, `provider_finish_reason`, `timings`, `reasoning_chars`, `tool_calls` (with `raw_arguments`, `parse_error`). `tool_requested` gains `raw_arguments` and `parse_error`.
- **Schema version.** `run_started.schema_version = 2`. Replay of a version-1 (Phase 1, Anthropic-shaped) run is refused with a clear message; there are only test and demo runs of version 1.
- **Replay** serves recorded normalised `ModelResponse`s through a `ReplayBackend` and compares normalised requests. The semantics measured in E010 do not change. Replay needs no runtime and no model.
- **Budgets.** Local runs cost $0, so the spend cap only applies to backends with a price. New runaway guards for all backends: `max_wall_seconds` per run and `request_timeout_s` per model call (a CPU model can take minutes per turn). Token limits stay. `limit_reached` events as today.
- **Metrics** gain seconds per turn, prompt tokens per second, generated tokens per second, cached prompt tokens (`timings`), and peak server RAM when measurable.

## 7. Configuration and enabling rules

Task files get a `[model]` section (replacing `[agent].model`):

```toml
[model]
backend = "openai_compat"            # scripted | openai_compat | anthropic
base_url = "http://127.0.0.1:8080"   # loopback only unless allow_remote = true
model = "qwen3-1.7b-q4_k_m"          # name the server reports
model_sha256 = "<sha256 of the GGUF>"
tool_strategy = "native"             # native | constrained_json
temperature = 0.0
seed = 1
options = { enable_thinking = false }
```

Anthropic stays possible but closed by default. A run with `backend = "anthropic"` starts only if **all** hold: the optional extra is installed (`uv sync --extra anthropic`), `AGENTLAB_ENABLE_EXTERNAL_API=1` is set, and a key is present. Otherwise: a clear refusal, no network call. The test suite never needs any of it.

Dependencies after the change: **core = Python stdlib only** (HTTP via `urllib.request`); `anthropic` moves to an optional extra; dev = `pytest`, `ruff`.

## 8. Implementation plan (steps 1-7 DONE 2026-10-07; 8-10 PROPOSED)

| # | Step | Tests (offline) |
|---|---|---|
| 1 | `model_types.py`: the types in §2, plus JSON (de)serialisation for the journal | round-trip, `arguments=None` handling |
| 2 | `ScriptedBackend` on neutral scripts; convert `tasks/demo-permissions/scripted.json` and test helpers | existing behaviour tests rewritten to neutral shapes |
| 3 | Agent loop + kernel on neutral types (`kernel.handle(ToolCall) -> ToolResult`); malformed-argument path | all Phase 1 behaviour tests pass again; new malformed-call tests |
| 4 | Gateway over `ModelBackend`: prefix check on neutral messages, `ModelError` retries, model check, wire + normalised blobs | gateway tests ported |
| 5 | Budgets: per-backend prices (local/scripted = 0), `max_wall_seconds`, `request_timeout_s` | limit tests |
| 6 | Replay on neutral recordings; `schema_version` 2; refuse v1 | E010 rerun (`run_e010.py`) must still give the same 9 outcomes |
| 7 | `OpenAICompatBackend` (stdlib `urllib`), strategies `native` and `constrained_json` | against a stub HTTP server in the test process that returns recorded llama-server-shaped responses |
| 8 | `AnthropicBackend` moved behind the optional extra and the enabling rules; `anthropic` import only inside it | refusal-without-flag test; adapter mapping tests with SDK types only when the extra is installed |
| 9 | `agent-lab local check` (health, props, apply-template preview, file hash); `docs/LOCAL-LLM.md` setup finalised | stub server |
| 10 | Owner installs runtime + model by hand (LOCAL-LLM §6). Run **E020**, then E021 → E024 | live-local tests marked `local`, deselected by default |

Stop points: after step 6 (kernel provider-neutral, nothing local yet) and after step 9 (ready for the owner to install).

## 9. Risks of the refactor itself

- Rewriting every test's response shape is the largest diff. Mitigation: steps 2-4 change shapes, not behaviour; the Phase 1 behaviour list in `ROADMAP.md` is the acceptance list.
- The neutral type set may miss something a backend needs. `provider_payload` and `options` are the escape hatches; anything used twice gets promoted to a real field.
- The OpenAI-compatible convention differs between servers. Each server is verified by an experiment before it is called supported.
