"""The model gateway: the one place the loop's requests leave for a backend.

It works the same for every backend (scripted, local, optional external API):

1. checks the request only *appends* to the previous one: same system prompt, same tools,
   same model, earlier messages unchanged. Some APIs reject edited histories, local servers
   reuse their cache only for an unchanged prefix, and replay needs it. A violation is a
   kernel bug, so it fails before anything is sent.
2. journals ``model_request``: the neutral request and the exact wire body the backend will
   send, both as blobs.
3. asks the backend, retrying ``transient`` errors itself (each attempt is an event);
   ``unavailable`` (e.g. local server not running) and ``permanent`` errors fail at once.
4. journals ``model_response``: finish reason, usage, tool calls with their raw arguments
   and parse errors, timings, the full response as a blob.
5. refuses a response from a model other than the one requested. No backend may silently
   switch models (D-018).
"""

from __future__ import annotations

import time

from agentlab.journal import Recorder, canonical_json
from agentlab.model_types import ModelBackend, ModelError, ModelRequest, ModelResponse


class ModelCallFailed(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason  # model_error | model_mismatch | prefix_violation
        self.message = message


class ModelGateway:
    def __init__(
        self, backend: ModelBackend, rec: Recorder, *, max_attempts: int = 3, sleep=time.sleep
    ):
        self.backend = backend
        self.rec = rec
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.turn = 0
        self._previous: dict | None = None  # last request sent, as a plain dict

    def _check_prefix(self, current: dict) -> None:
        prev = self._previous
        if prev is None:
            return
        for key in ("system", "tools", "model"):
            if canonical_json(current[key]) != canonical_json(prev[key]):
                raise ModelCallFailed("prefix_violation", f"{key!r} changed between requests")
        old, new = prev["messages"], current["messages"]
        if len(new) < len(old) or canonical_json(new[: len(old)]) != canonical_json(old):
            raise ModelCallFailed("prefix_violation", "earlier messages were edited or removed")

    def call(self, request: ModelRequest) -> ModelResponse:
        current = request.to_dict()
        try:
            self._check_prefix(current)
        except ModelCallFailed as e:
            self.rec.emit(
                "model_error", {"kind": "kernel", "error_type": e.reason, "message": e.message}
            )
            raise

        self.turn += 1
        self.rec.emit(
            "model_request",
            {
                "turn": self.turn,
                "backend": self.backend.name,
                "model": request.model,
                "max_output_tokens": request.max_output_tokens,
                "temperature": request.temperature,
                "seed": request.seed,
                "options": request.options,
                "n_messages": len(request.messages),
                "tools": [t.name for t in request.tools],
                "request_blob": self.rec.blob(current),
                "wire_blob": self.rec.blob(self.backend.wire_request(request)),
            },
        )

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.backend.complete(request)
                break
            except ModelError as e:
                info = {
                    "kind": e.kind,
                    "error_type": e.error_type,
                    "message": e.message[:1000],
                    "status_code": e.status,
                    "request_id": e.request_id,
                }
                if e.kind == "transient" and attempt < self.max_attempts:
                    delay = min(2**attempt, 30)
                    self.rec.emit("model_retry", {"attempt": attempt, "delay_s": delay, **info})
                    self.sleep(delay)
                    continue
                self.rec.emit("model_error", {"attempts": attempt, **info})
                raise ModelCallFailed("model_error", e.message) from e

        self.rec.emit(
            "model_response",
            {
                "turn": self.turn,
                "backend": self.backend.name,
                "requested_model": request.model,
                "actual_model": response.model,
                "finish_reason": response.finish_reason,
                "provider_finish_reason": response.provider_finish_reason,
                "usage": response.to_dict()["usage"],
                "request_id": response.request_id,
                "text": response.text,
                "tool_calls": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "arguments": c.arguments,
                        "raw_arguments": c.raw_arguments,
                        "parse_error": c.parse_error,
                    }
                    for c in response.tool_calls
                ],
                "reasoning_chars": len(response.reasoning or ""),
                "timings": response.timings,
                "response_blob": self.rec.blob(response.to_dict()),
            },
        )
        if response.model != request.model:
            raise ModelCallFailed(
                "model_mismatch",
                f"requested {request.model!r} but the response came from {response.model!r}; "
                "model fallback is disabled",
            )
        self._previous = current
        return response
