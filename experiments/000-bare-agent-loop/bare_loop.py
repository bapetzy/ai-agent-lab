"""E000: the bare agent loop. No kernel, no journal, no framework: just the API.

    uv run python experiments/000-bare-agent-loop/bare_loop.py

OPTIONAL and Anthropic-specific (free-first, D-026): running it live needs the `anthropic`
extra (uv sync --extra anthropic) and a paid ANTHROPIC_API_KEY. It is never required. Its loop
logic is tested offline without either (tests/test_e000_bare_loop.py). A local-model variant
is planned after the llama.cpp adapter exists.

Prints the exact JSON that goes to the model and comes back, at every step.
"""

import json
import os
import sys

MODEL = "claude-opus-5-5"
NOTES = {  # the "world" the tool can see
    "shopping": "milk, eggs, coffee",
    "todo": "call Ana about the venue; send Ben the agenda",
}
TOOLS = [
    {
        "name": "read_note",
        "description": "Read one of the user's notes by name. Available names: shopping, todo.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "note name"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    }
]


def show(label: str, obj) -> None:
    print(f"\n===== {label} =====")
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def bare_loop(client, prompt: str, max_turns: int = 4) -> str:
    messages = [{"role": "user", "content": prompt}]
    for turn in range(1, max_turns + 1):
        request = {
            "model": MODEL,
            "max_tokens": 2000,
            "output_config": {"effort": "low"},
            "tools": TOOLS,
            "messages": messages,
        }
        show(f"turn {turn}: REQUEST (what the model receives; it remembers nothing else)", request)
        response = client.messages.create(**request).to_dict()
        show(f"turn {turn}: RESPONSE", response)
        messages.append({"role": "assistant", "content": response["content"]})

        if response["stop_reason"] != "tool_use":
            return "".join(b.get("text", "") for b in response["content"] if b["type"] == "text")

        results = []
        for block in response["content"]:
            if block["type"] == "tool_use":  # the model only ASKS; we run the tool
                name = block["input"]["name"]
                found = name in NOTES
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": NOTES[name] if found else f"no note named {name!r}",
                        "is_error": not found,
                    }
                )
        messages.append({"role": "user", "content": results})
    return "(stopped: max_turns reached)"


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (E000 calls the real API).")
    import anthropic  # optional extra, imported only when actually running live

    client = anthropic.Anthropic(max_retries=0)
    final = bare_loop(client, "What is on my todo note? Answer in one sentence.")
    print(f"\n===== FINAL ANSWER =====\n{final}")
