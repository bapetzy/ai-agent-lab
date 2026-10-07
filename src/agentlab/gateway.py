"""The model gateway: the only code that talks to the model.

For every call it:

1. checks the request only *appends* to the previous one (same system prompt, same tools,
   earlier messages byte-identical). Opus 5.5 rejects edited histories, and replay needs
   it. A violation is a kernel bug, so it fails loudly before anything is sent.
2. journals ``model_request`` (full request body stored as a blob; no API key in it: the
   SDK puts the key in an HTTP header, which we never record).
3. calls ``client.messages.create`` and retries transient errors itself (the SDK's own
   retries are turned off) so every attempt is a ``model_retry`` event.
4. journals ``model_response`` (full response as a blob, usage, stop reason, request id,
   requested and actual model).
5. refuses a response from a model other than the one requested. We never send the
   ``fallbacks`` parameter, so the API should not switch models; if it ever does, the run
   fails instead of silently continuing on another model.

Thinking blocks come back with empty text (the API's default ``display: "omitted"``) and an
opaque signature. They are stored and passed back unchanged; we never see or log reasoning.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from agentlab.journal import Recorder, canonical_json

RETRYABLE_STATUS = {408, 409, 429}


class ModelCallFailed(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason  # api_error | model_mismatch | prefix_violation
        self.message = message


def _is_retryable(e: anthropic.APIError) -> bool:
    if isinstance(e, anthropic.APIConnectionError):  # includes timeouts
        return True
    status = getattr(e, "status_code", None)
    return status is not None and (status in RETRYABLE_STATUS or status >= 500)


def _error_info(e: Exception) -> dict:
    return {
        "error_type": type(e).__name__,
        "message": str(e)[:1000],
        "status_code": getattr(e, "status_code", None),
        "request_id": getattr(e, "request_id", None),
    }


class ModelGateway:
    def __init__(self, client: Any, rec: Recorder, *, max_attempts: int = 3, sleep=time.sleep):
        self.client = client
        self.rec = rec
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.turn = 0
        self._previous: dict | None = None  # last request that was sent

    def _check_prefix(self, params: dict) -> None:
        prev = self._previous
        if prev is None:
            return
        for key in ("system", "tools", "model"):
            if canonical_json(params.get(key)) != canonical_json(prev.get(key)):
                raise ModelCallFailed("prefix_violation", f"{key!r} changed between requests")
        old, new = prev["messages"], params["messages"]
        if len(new) < len(old) or canonical_json(new[: len(old)]) != canonical_json(old):
            raise ModelCallFailed("prefix_violation", "earlier messages were edited or removed")

    def call(self, params: dict) -> dict:
        try:
            self._check_prefix(params)
        except ModelCallFailed as e:
            self.rec.emit("model_error", {"error_type": e.reason, "message": e.message})
            raise

        self.turn += 1
        self.rec.emit(
            "model_request",
            {
                "turn": self.turn,
                "model": params["model"],
                "max_tokens": params.get("max_tokens"),
                "output_config": params.get("output_config"),
                "thinking": params.get("thinking"),
                "n_messages": len(params["messages"]),
                "tools": [t["name"] for t in params.get("tools", [])],
                "request_blob": self.rec.blob(params),
            },
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                message = self.client.messages.create(**params)
                break
            except anthropic.APIError as e:
                info = _error_info(e)
                if _is_retryable(e) and attempt < self.max_attempts:
                    delay = min(2**attempt, 30)
                    self.rec.emit("model_retry", {"attempt": attempt, "delay_s": delay, **info})
                    self.sleep(delay)
                    continue
                self.rec.emit("model_error", {"attempts": attempt, **info})
                raise ModelCallFailed("api_error", info["message"]) from e

        data = message.to_dict()
        actual = data.get("model")
        self.rec.emit(
            "model_response",
            {
                "turn": self.turn,
                "requested_model": params["model"],
                "actual_model": actual,
                "stop_reason": data.get("stop_reason"),
                "stop_details": data.get("stop_details"),
                "usage": data.get("usage"),
                "request_id": getattr(message, "_request_id", None),
                "message_id": data.get("id"),
                "content_types": [b.get("type") for b in data.get("content", [])],
                "tool_uses": [
                    {"id": b["id"], "name": b["name"], "input": b["input"]}
                    for b in data.get("content", [])
                    if b.get("type") == "tool_use"
                ],
                "text": "".join(
                    b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                ),
                "response_blob": self.rec.blob(data),
            },
        )
        if actual != params["model"]:
            raise ModelCallFailed(
                "model_mismatch",
                f"requested {params['model']!r} but the response came from {actual!r}; "
                "model fallback is disabled",
            )
        self._previous = params
        return data
