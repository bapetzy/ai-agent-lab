"""A fake model: returns pre-written responses instead of running a model.

Used by the tests, by offline demos (``agent-lab run ... --scripted file.json``), and as
the pattern replay builds on. Scripts are provider-neutral JSON: one item per model call.

    {"text": "...",
     "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "a.md"}},
                    {"id": "c2", "name": "read_file", "raw_arguments": "{broken"}],
     "finish_reason": "tool_calls",        # optional: inferred from tool_calls
     "usage": {"input_tokens": 0, "output_tokens": 0},
     "model": "scripted-fake"}
    {"error": {"kind": "transient", "message": "busy", "status": 529}}

Runs that use it record backend "scripted", so they are never mistaken for real model output.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentlab.model_types import ModelError, ModelRequest, ModelResponse, ToolCall, Usage

DEFAULT_MODEL = "scripted-fake"


def call(
    name: str, arguments: dict | None = None, id: str = "c1", *, raw: str | None = None
) -> dict:
    """A scripted tool call. Pass ``raw`` to script malformed arguments."""
    if raw is not None:
        return {"id": id, "name": name, "raw_arguments": raw}
    return {"id": id, "name": name, "arguments": arguments if arguments is not None else {}}


def reply(
    text: str = "",
    *,
    tool_calls: list[dict] | None = None,
    finish_reason: str | None = None,
    model: str = DEFAULT_MODEL,
    usage: dict | None = None,
    reasoning: str | None = None,
) -> dict:
    return {
        "text": text,
        "tool_calls": tool_calls or [],
        "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
        "model": model,
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
        "reasoning": reasoning,
    }


def _to_response(item: dict) -> ModelResponse:
    calls = []
    for c in item.get("tool_calls", []):
        if "raw_arguments" in c:
            calls.append(ToolCall.from_raw(c["id"], c["name"], c["raw_arguments"]))
        else:
            calls.append(ToolCall.from_object(c["id"], c["name"], c.get("arguments", {})))
    return ModelResponse(
        text=item.get("text", ""),
        tool_calls=tuple(calls),
        finish_reason=item.get("finish_reason") or ("tool_calls" if calls else "stop"),
        usage=Usage(**{"input_tokens": 0, "output_tokens": 0, **item.get("usage", {})}),
        model=item.get("model", DEFAULT_MODEL),
        reasoning=item.get("reasoning"),
        raw={"scripted": item},
    )


class ScriptedBackend:
    name = "scripted"

    def __init__(self, script: list):
        self.script = list(script)
        self.total = len(self.script)
        self.requests: list[dict] = []  # every request received (plain dicts)

    def wire_request(self, request: ModelRequest) -> dict:
        return request.to_dict()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(json.loads(json.dumps(request.to_dict())))
        if not self.script:
            raise ModelError(
                "permanent",
                f"no scripted response left for request #{len(self.requests)}",
                error_type="ScriptExhausted",
            )
        item = self.script.pop(0)
        if isinstance(item, ModelError):
            raise item
        if "error" in item:
            err = item["error"]
            raise ModelError(
                err["kind"],
                err["message"],
                status=err.get("status"),
                error_type=err.get("error_type"),
            )
        return _to_response(item)

    def describe(self) -> dict:
        return {"name": self.name, "script_items": self.total}

    def prices(self, model: str) -> dict | None:
        return None  # free


def load_script(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
