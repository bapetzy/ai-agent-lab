# Local LLM: runtime and model decision

Status (2026-10-07): runtime and model choices **approved** as candidates (D-028, D-029). The adapter that talks to `llama-server` is **IMPLEMENTED** (`backends/openai_compat.py`, tested against a stub). No runtime or model has been installed or downloaded. Every performance number below is NOT VERIFIED until E020 runs.

## 1. The machine (Observed, 2026-10-07)

| Item | Value | Consequence |
|---|---|---|
| CPU | AMD Ryzen 7 5825U, 8 cores / 16 threads, AVX2 + FMA + F16C, no AVX-512 | Standard CPU inference works; the usual x64 builds apply |
| GPU | AMD Radeon integrated (Vega-class), 2 GB carved out of system RAM | No discrete GPU. Vulkan offload might help prompt processing; it shares the same memory bus, so it cannot speed up token generation much. NOT VERIFIED. |
| RAM | 8 GB physical in **one DIMM** (DDR4-3200, channel B only, so single channel); 5.85 GB visible to Windows; **0.58 GB free** at the time of the audit (Cursor, Chrome, Memory Compression) | RAM is the binding constraint. Running a model means closing other apps. |
| Memory bandwidth | Single-channel DDR4-3200 = 25.6 GB/s theoretical | Generating each token reads all weights once. Upper bound ≈ 25.6 GB/s ÷ model size: ~23 tok/s for a 1.1 GB model, ~10 tok/s for 2.5 GB. Real numbers are usually well below the bound. (Inferred arithmetic, NOT VERIFIED.) |
| Disk | C: ~26 GB free, D: ~106 GB free | Store models on D: |
| WSL2 | Ubuntu 24.04, VM sees ~2.8 GB RAM | Running inference inside WSL would get *less* memory. Run natively on Windows. |
| Docker | Engine 29.7 inside WSL | Not useful for inference here (same 2.8 GB cap, extra overhead) |
| Local runtimes installed | None (`ollama`, `llama-server`, `lms` absent) | Clean slate |
| Python | 3.14 via `uv` | Stdlib `urllib` is enough to call a local HTTP server; no new dependency needed |

## 2. Runtimes considered

All facts below were read in each project's own docs or release metadata on 2026-10-07 unless marked otherwise.

| | **llama.cpp `llama-server`** | **Ollama** | **LM Studio** | **HF Transformers** |
|---|---|---|---|---|
| Licence / cost | MIT, free (GitHub API) | MIT, free (GitHub API) | Free for home and work use; proprietary, closed source (vendor statements via secondary sources) | Apache 2.0, free |
| Windows install | Portable zip, no installer, no service: `win-cpu-x64` 19.4 MB, `win-vulkan-x64` 33.4 MB (build b11461, 2026-10-07) | Installer or zip; `ollama-windows-amd64.zip` 1.47 GB (bundles GPU libraries; v0.40.0); runs a background service | Desktop app + `lms` CLI / headless `llmster` | Python + PyTorch (multi-GB) |
| AMD iGPU | Vulkan build available | "Additional GPU support … via Vulkan … enabled by default" (Ollama GPU docs); no statement about Vega iGPUs | Vulkan (not checked) | ROCm/DirectML paths; not realistic here |
| CPU-only | Yes (primary target of the project) | Yes | Yes | Yes, but slow and RAM-hungry (bf16 weights: ~3.4 GB for 1.7B) |
| Local HTTP API | OpenAI-compatible `/v1/chat/completions`, plus `/health`, `/props`, `/tokenize`, `/apply-template`, `/metrics` | Native `/api/chat`, plus an OpenAI-compatible endpoint | OpenAI-compatible + own REST API | None built in |
| Tool calling | Native, via the model's chat template with `--jinja`; native parsers for Llama 3.x, Hermes 2/3, Qwen 2.5, Mistral Nemo, Functionary and others; generic fallback for other templates; parallel calls opt-in (`function-calling.md`) | Native: `tools` in `/api/chat`; response `tool_calls[].function.{name, arguments}` with arguments as an **object** and no call id; results sent back as `{"role": "tool", "tool_name", "content"}` (Ollama tool-calling docs) | Supported; described by secondary sources as beta-quality for many model families | Do-it-yourself: render template, parse output |
| Structured output | `response_format` with a JSON schema, which the server **converts to a grammar** (constrained decoding); hand-written GBNF grammars too | `format` = JSON schema. The docs do not say whether decoding is constrained and advise validating the result yourself | JSON schema (not checked) | Via extra libraries |
| Observability | `timings` per response (`cache_n`, `prompt_n`, `prompt_ms`, `predicted_n`, `predicted_per_second`, …); `/apply-template` returns the exact prompt text a request becomes; `/tokenize`; `reasoning_content` split out with `--reasoning-format` | Durations and token counts in responses (not checked in detail) | GUI logs | Full access to tokens and logits |
| Reproducibility | Pin build number + GGUF file SHA-256; `seed`; temperature 0 | Model tags can move; digests can be pinned; the app auto-updates | App auto-updates | Pin versions; heaviest stack |
| Model source | GGUF files from Hugging Face (manual download, or `-hf repo:quant`) | `ollama pull` from its registry | Built-in browser | HF safetensors |
| Learning value | High: you see the chat template, the grammar, the cache, the server flags | Medium: convenient; hides the template and the engine | Low for this lab (GUI, closed) | High in theory, impractical on 5.85 GB |

Ruled out early:

- **Transformers**: PyTorch alone is gigabytes; bf16 weights double the RAM of a Q4 GGUF; CPU speed far below llama.cpp. Good for a different lab.
- **LM Studio**: free but closed source; the lab should be able to read and pin what it runs on. It speaks the same OpenAI-compatible protocol, so it can still be tried later through the same adapter.
- **Running inside WSL2 or Docker**: less RAM (2.8 GB cap), no benefit.

## 3. Decision: llama.cpp `llama-server` (native Windows), CPU build first

**Recommended primary runtime: llama.cpp `llama-server`, Windows x64 CPU build, pinned to one build number. Vulkan build as an experiment arm.**

Why it wins *for this lab on this machine*:

1. **Smallest footprint.** A 19 MB zip, no installer, no background service, no auto-update. On a machine with half a gigabyte free, nothing should run when no experiment runs.
2. **Most observable.** `/apply-template` shows the literal prompt a tool-calling request turns into: you can see *how* tool definitions reach the model, which is the core of "how do agents use tools". `timings.cache_n` shows how much of the prompt was served from cache, which turns the append-only rule into a measurable speed effect.
3. **Real constrained decoding, documented.** JSON-schema `response_format` is compiled to a grammar. That gives a second, robust tool-calling strategy (§5) and an experiment comparing the two (E024).
4. **Protocol, not product.** It speaks the OpenAI-compatible chat API. The adapter we write for it will also talk to Ollama and LM Studio (NOT VERIFIED until tried), so switching runtime is a config change.
5. **Pinnable.** Build number + GGUF SHA-256 recorded in `run_started` make a local run as reproducible as a local run can be.

What Ollama does better, honestly: one-command model downloads, a curated model list, less to configure. It is the documented fallback if llama.cpp setup proves painful. Ollama's native API has no tool-call ids and passes arguments as an object; its OpenAI-compatible endpoint would be used instead so the same adapter works.

## 4. Decision: model

**Recommended model: Qwen3-1.7B, GGUF Q4_K_M (~1.1 GB), thinking mode off by default.**

| Candidate | File size (Q4_K_M unless noted) | Licence | Tool calling evidence | Fit on this machine |
|---|---|---|---|---|
| **Qwen3-1.7B** | 1,107 MB (`unsloth/Qwen3-1.7B-GGUF`); official `Qwen/Qwen3-1.7B-GGUF` only ships Q8_0, 1,834 MB | Apache 2.0 | BFCL overall 55.5% (prompt mode) but **multi-turn 16.9%** (TinyLLM paper, arXiv 2511.22138; Reported) | Weights + KV cache ≈ 1.6 GB at 4k context, ≈ 2.0 GB at 8k (computed below). Fits with apps closed. |
| Qwen3-4B | 2,497 MB (official) | Apache 2.0 | BFCL overall 62.0% (same source; Reported) | ≈ 3.1 GB at 4k, ≈ 3.7 GB at 8k. Only with nearly everything closed; heavy paging likely. Stretch arm in E020. |
| Llama 3.2 3B | 2.0 GB (Ollama tag) | Llama 3.2 Community Licence (custom, not OSI) | Native llama.cpp tool parser; Meta reports tool use as a strength (no numbers on the page read) | ≈ 2.5-3 GB. Comparison candidate. |
| Granite 4.1 3B | 2.1 GB (Ollama tag) | Apache 2.0 | Described as tool/function-calling focused; no BFCL number found on its page | ≈ 2.5-3 GB. Comparison candidate. |
| Qwen3-0.6B | 523 MB (Ollama tag) | Apache 2.0 | Weak; useful only to test plumbing | Fits easily. Smoke tests only. |
| Gemma 4 E2B / E4B | 4.6-9.5 GB (Ollama tags, multimodal) | Not checked | "native function-calling" per Ollama page | Too large for this machine |

KV cache arithmetic (from the official `config.json` files, verified): Qwen3-1.7B has 28 layers, 8 KV heads, head dim 128 → 114,688 bytes per token in f16 → **448 MiB at 4,096 tokens, 896 MiB at 8,192**. Qwen3-4B: 36 layers → 576 MiB / 1,152 MiB. Add the weights and roughly 0.1-0.3 GB of compute buffers (estimate).

Why Qwen3-1.7B:

- It is the largest model that fits *comfortably* (with headroom for Windows itself) given 5.85 GB visible and single-channel bandwidth.
- Apache 2.0, from a major lab, with an official GGUF repository (Q8_0) to cross-check the third-party Q4_K_M against.
- Hermes-style tool-call format, close to Qwen 2.5's, for which llama.cpp has a native parser. Whether Qwen3's template is recognised natively or handled by the generic fallback is NOT VERIFIED; E021 checks via `/props` and `/apply-template`.
- Switchable thinking: `chat_template_kwargs: {"enable_thinking": false}` (verified as a server request parameter) avoids spending minutes of CPU on reasoning text; thinking-on is an experiment arm, and unlike the Anthropic API its reasoning text is returned (`reasoning_content`) and can be journaled, labelled as model output.

Honest expectation: a 1.7B model is far weaker than Opus 5.5. The reported multi-turn function-calling score (16.9%) predicts that multi-step agent tasks will often fail. For a lab whose goal is to *observe* agent mechanics and failure modes, that is acceptable, even useful: E022 exists to catalogue those failures. If E020 shows enough RAM headroom, Qwen3-4B becomes the default.

Quantisation choice: Q4_K_M from `unsloth` for speed and RAM; official Q8_0 (1.8 GB) as a fidelity check in E020. Every file is pinned by SHA-256.

## 5. Tool calling with a local model

Three ways to get a tool call out of a local model:

| Strategy | How | Robustness | Educational value | Decision |
|---|---|---|---|---|
| **A. Native tool calling** | Send `tools` in the OpenAI-compatible request; `llama-server --jinja` renders them through the model's own chat template (the format it was trained on) and parses the reply into `tool_calls` | Depends on the model and on the server's parser. Whether arguments are grammar-constrained during generation is not stated in the docs read: NOT VERIFIED | High: the same structure as hosted APIs; `/apply-template` shows the rendered prompt | **Primary** |
| **B. Constrained JSON action** | No `tools` field. The kernel asks for one JSON "action" per turn (`{"type": "tool_call", "tool": …, "arguments": {…}}` or `{"type": "final", "text": …}`) with `response_format` = JSON schema, which `llama-server` compiles into a grammar | Output is always parseable and schema-valid (the tool name is an enum; arguments follow each tool's schema). Not in the model's trained format, so quality may drop; one call per turn | High: shows constrained decoding concretely | **Fallback and experiment arm** (EXPERIMENTAL) |
| C. Free-text parsing | Ask the model to write a call in text and regex it out | Fragile | Low | Rejected (kept only as the naive arm of E024 if useful) |

In all three, the result is normalised into the same `ToolCall(id, name, arguments)` before the kernel sees it (`MODEL-GATEWAY.md`). A call whose arguments do not parse or do not match the schema is still journaled and goes back to the model as an `invalid_input` error; it is never executed. Nothing in the runtime can execute a tool: `llama-server` only produces text.

## 6. Setup procedure (PROPOSED: run by the owner, nothing automatic)

To be finalised in implementation step 9 (`MODEL-GATEWAY.md` §8):

1. Download `llama-<build>-bin-win-cpu-x64.zip` from the llama.cpp GitHub release page; record the build number; unzip to `D:\agentlab\runtimes\llama.cpp\<build>\`.
2. Download `Qwen3-1.7B-Q4_K_M.gguf` (~1.1 GB) to `D:\agentlab\models\`; record its SHA-256 (`Get-FileHash`).
3. Close memory-heavy apps. Start the server in its own terminal so its log is visible:
   `llama-server -m D:\agentlab\models\Qwen3-1.7B-Q4_K_M.gguf --jinja -c 4096 -np 1 --host 127.0.0.1 --port 8080 --alias qwen3-1.7b-q4_k_m`
   (`--alias`: the name the server should report, which must equal the task's `[model].name`, because the kernel refuses responses from a model other than the one requested. NOT VERIFIED that llama-server reports the alias in its responses.)
   (`-np 1`: one slot, so the context is not split or multiplied; `--host 127.0.0.1`: never reachable from the network.)
4. `uv run agent-lab local check` (PROPOSED) calls `/health`, `/props`, `/apply-template` and prints what the kernel will talk to.

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Too slow to be pleasant (prompt processing on CPU; first turn re-reads ~1-2k tokens of system prompt + tool schemas) | High | Prefix caching (append-only transcript), 4k context, thinking off, short tool descriptions; Vulkan arm in E020 |
| Out of memory / heavy paging | Medium-high | 1.7B Q4 default, `-c 4096`, `-np 1`, close apps, measure peak RAM in E020; `-ctk q8_0 -ctv q8_0` halves KV (llama.cpp warns extreme KV quantisation hurts tool calling; q8_0 is moderate; NOT VERIFIED) |
| Weak multi-step tool use at 1.7B | High | Treat as a finding (E022); constrained-JSON strategy (E024); try 4B or a 3B tool-focused model |
| Qwen3 template not natively parsed by the server | Medium | Check `/props`; generic fallback; strategy B |
| Non-determinism even at temperature 0 (threads, batching, build changes) | Medium | Replay never re-runs the model; E023 measures run-to-run variation |
| Security software on this machine interferes with unsigned executables (it already blocks link creation from `python.exe`) | Unknown | Observe at install; the zip is official GitHub release output |
| Supply chain: model file or binary tampered | Low | Official GitHub releases; known Hugging Face repos; SHA-256 recorded and checked at run start |
| Server reachable from the network | Low if configured | Always `--host 127.0.0.1`; the kernel refuses non-loopback base URLs unless explicitly allowed |
| Runtime API drift (llama.cpp ships several builds a day) | High | Pin one build; upgrade deliberately; record build in every run |

## Sources

- llama.cpp server README and `docs/function-calling.md` (ggml-org/llama.cpp, read 2026-10-07); release assets via the GitHub API (build b11461).
- Ollama docs: tool calling, structured outputs, GPU (docs.ollama.com, read 2026-10-07); release v0.40.0 via the GitHub API.
- Hugging Face API: `Qwen/Qwen3-1.7B`, `Qwen/Qwen3-4B` configs; GGUF file lists for `Qwen/Qwen3-1.7B-GGUF`, `Qwen/Qwen3-4B-GGUF`, `unsloth/Qwen3-1.7B-GGUF`.
- Ollama library pages: qwen3, gemma4, granite4, granite4.1, llama3.2 (sizes, licences).
- TinyLLM: Evaluation and Optimization of Small Language Models for Agentic Tasks on Edge Devices, arXiv 2511.22138 (BFCL numbers as reported in search results; paper not read in full).
- LM Studio licence and tool-calling quality: secondary sources only.
