"""OPTIONAL adapter for Anthropic's Messages API. Never required (free-first, D-026).

Off unless: the `anthropic` extra is installed, AGENTLAB_ENABLE_EXTERNAL_API=1, the task says
backend = "anthropic", and a key is present (see backends/__init__.py). This is the only
module in the project that knows Anthropic's wire format. The SDK is imported lazily, so the
mapping functions below work (and are tested) without the SDK installed.

Mapping (docs/MODEL-GATEWAY.md §4.2):
- system -> top-level ``system``; tools -> {name, description, input_schema, strict}
- assistant turns -> the original content blocks from ``provider_payload`` when present
  (thinking blocks with signatures must go back unchanged), else text + tool_use blocks
- tool results -> one user message of tool_result blocks
- options.effort -> output_config.effort; seed is not supported by this API (ignored)
- stop_reason tool_use / end_turn / max_tokens / refusal -> tool_calls / stop / length / refusal
"""

from __future__ import annotations

from agentlab.model_types import (
    Message,
    ModelError,
    ModelRequest,
    ModelResponse,
    ToolCall,
    Usage,
)

# USD per million tokens (list prices, claude-api skill cached 2026-09-25). Re-check before use.
PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}

FINISH = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "max_tokens": "length",
    "refusal": "refusal",
}
RETRYABLE_STATUS = {408, 409, 429}


def _assistant_content(m: Message) -> list[dict]:
    if m.provider_payload and "content" in m.provider_payload:
        return m.provider_payload["content"]
    blocks: list[dict] = [{"type": "text", "text": m.text}] if m.text else []
    blocks += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments or {}}
        for c in m.tool_calls
    ]
    return blocks


def to_wire(request: ModelRequest) -> dict:
    messages = []
    for m in request.messages:
        if m.role == "user":
            messages.append({"role": "user", "content": m.text})
        elif m.role == "assistant":
            messages.append({"role": "assistant", "content": _assistant_content(m)})
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.call_id,
                            "content": r.content,
                            "is_error": r.is_error,
                        }
                        for r in m.tool_results
                    ],
                }
            )
    wire = {
        "model": request.model,
        "max_tokens": request.max_output_tokens,
        "system": request.system,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
                "strict": True,
            }
            for t in request.tools
        ],
        "messages": messages,
    }
    if request.temperature is not None:  # newer Opus models reject sampling params
        wire["temperature"] = request.temperature
    if "effort" in request.options:
        wire["output_config"] = {"effort": request.options["effort"]}
    return wire


def response_from_wire(data: dict, request_id: str | None = None) -> ModelResponse:
    content = data.get("content", [])
    u = data.get("usage") or {}
    return ModelResponse(
        text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
        tool_calls=tuple(
            ToolCall.from_object(b["id"], b["name"], b.get("input"))
            for b in content
            if b.get("type") == "tool_use"
        ),
        finish_reason=FINISH.get(data.get("stop_reason"), "other"),
        usage=Usage(
            input_tokens=u.get("input_tokens") or 0,
            output_tokens=u.get("output_tokens") or 0,
            cached_input_tokens=u.get("cache_read_input_tokens") or 0,
            cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
        ),
        model=data.get("model", ""),
        provider_finish_reason=data.get("stop_reason"),
        raw=data,
        provider_payload={"content": content},
        request_id=request_id,
    )


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, *, api_key: str, client=None, timeout: float = 300.0):
        self.api_key = api_key  # registered with the journal as a secret; never recorded
        self.timeout = timeout
        self._client = client

    def _sdk_client(self):
        if self._client is None:
            import anthropic  # lazy: optional extra

            # SDK retries off: the gateway retries and journals every attempt itself.
            self._client = anthropic.Anthropic(
                api_key=self.api_key, max_retries=0, timeout=self.timeout
            )
        return self._client

    def wire_request(self, request: ModelRequest) -> dict:
        return to_wire(request)

    def complete(self, request: ModelRequest) -> ModelResponse:
        import anthropic  # lazy: optional extra

        try:
            message = self._sdk_client().messages.create(**to_wire(request))
        except anthropic.APIError as e:
            status = getattr(e, "status_code", None)
            transient = isinstance(e, anthropic.APIConnectionError) or (
                status is not None and (status in RETRYABLE_STATUS or status >= 500)
            )
            raise ModelError(
                "transient" if transient else "permanent",
                str(e)[:1000],
                status=status,
                request_id=getattr(e, "request_id", None),
                error_type=type(e).__name__,
            ) from e
        return response_from_wire(message.to_dict(), getattr(message, "_request_id", None))

    def describe(self) -> dict:
        import importlib.metadata

        try:
            version = importlib.metadata.version("anthropic")
        except importlib.metadata.PackageNotFoundError:
            version = None
        return {"name": self.name, "sdk_version": version}

    def prices(self, model: str) -> dict:
        if model not in PRICES_PER_MTOK:
            raise ValueError(
                f"no price known for model {model!r}; add it to PRICES_PER_MTOK "
                "so the spend limit can be enforced"
            )
        return PRICES_PER_MTOK[model]
