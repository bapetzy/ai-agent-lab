"""A fake model: returns pre-written responses instead of calling the API.

Used by the tests, by offline demos (``agent-lab run ... --scripted file.json``), and as
the shape that replay builds on. Runs that use it record ``model_backend: "scripted"`` so
they can never be mistaken for real model behaviour.

The responses are real ``anthropic.types.Message`` objects built from plain dicts, so the
kernel sees exactly the same types it gets from the SDK.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic.types import Message

from agentlab.journal import canonical_json


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(name: str, input: dict, id: str) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def response(
    content: list[dict],
    stop_reason: str,
    *,
    model: str = "scripted-fake",
    usage: dict | None = None,
    id: str = "msg_scripted",
) -> dict:
    return {
        "id": id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
    }


class ScriptExhausted(RuntimeError):
    """The kernel asked the scripted model for more responses than the script has."""


class ScriptedClient:
    """Duck-types the part of ``anthropic.Anthropic`` the gateway uses: ``messages.create``."""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[dict] = []  # every request received, deep-copied
        self.messages = self

    def create(self, **params: Any) -> Message:
        self.requests.append(json.loads(canonical_json(params)))
        if not self.script:
            raise ScriptExhausted(f"no scripted response left for request #{len(self.requests)}")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Message.model_validate(item)


def load_script(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
