# E000 — Bare agent loop

**Status:** script ready and its loop logic tested offline. **Not yet run against the real API** (no API key on this machine as of 2026-10-07).

## Question

Can we observe a complete model → tool → result → model cycle without hiding the mechanism behind an agent framework (or behind our own kernel)?

## Hypothesis (written before the first live run)

1. One user question that needs a note will produce exactly two model calls: the first ends with `stop_reason: "tool_use"` and one `tool_use` block (`read_note` with `{"name": "todo"}`); the second ends with `"end_turn"` and a text answer.
2. The second request will contain the *entire* conversation again (user question, the assistant's `tool_use` turn, our `tool_result`), because the model keeps no memory between calls.
3. On Claude Opus 5.5 the first response will also contain a `thinking` block with **empty** text and a long opaque `signature` (thinking is always on; the default display is "omitted"). We pass it back unchanged and never see any reasoning.
4. Usage on the second call will show more input tokens than the first, because the whole history is resent.
5. The model's `tool_use.input` will validate against the schema (`strict: true`).

## Setup

- `bare_loop.py`: about 60 lines. Only dependency: the official `anthropic` SDK. No agentlab imports.
- Model `claude-opus-5-5`, effort `low`, `max_tokens` 2000, at most 4 model calls, SDK retries off.
- One tool, `read_note(name)`, reading from a Python dict. No filesystem, no shell, no network beyond the API call.
- Cost: at Opus 5.5 list prices ($4 / $20 per million tokens) a few thousand tokens cost well under $0.05.

## Procedure

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # this PowerShell session only
uv run python experiments/000-bare-agent-loop/bare_loop.py
```

Read the printed REQUEST/RESPONSE pairs top to bottom. For each hypothesis, note what you actually see.

Offline check of the loop logic (no API): `uv run pytest tests/test_e000_bare_loop.py`.

## Expected result

Two REQUEST/RESPONSE pairs, then `FINAL ANSWER` mentioning Ana and the agenda.

## Actual result (Observed)

Not yet run. Offline test passes (the loop sends the tool result back in the next request and stops at `end_turn`), which shows the *code* behaves as described. It shows nothing about the real model.

## Observations / Failures

None yet (no live run).

## Conclusion

Pending the live run. The script is the reference point for the kernel: everything in `src/agentlab/agent.py` is this loop plus a gateway, a kernel, budgets and a journal.

## New Questions

- How large is the thinking `signature` compared with the visible text, and how many input tokens does it add when passed back?
- Does the model call the tool at effort `low`, or answer from the description alone? (The tool description lists the note names.)
- What changes in the response with `thinking: {"type": "adaptive", "display": "summarized"}`?
