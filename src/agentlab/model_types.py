"""The kernel's own vocabulary for talking to *any* model.

Every backend (scripted, a local llama.cpp server, an optional external API) translates
its wire format into these types and back. The agent loop and the kernel only ever see
these; they never import a provider SDK or look at a provider's JSON.

    ModelRequest  --backend-->  (provider wire format)  --backend-->  ModelResponse

A tool call is a *proposal* from the model. Its arguments may be broken JSON; we keep the
raw text and the parse error instead of hiding them, and the kernel refuses to run it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

FINISH_REASONS = ("tool_calls", "stop", "length", "refusal", "other")
ROLES = ("user", "assistant", "tool_results")
ERROR_KINDS = ("transient", "permanent", "unavailable")


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about one tool."""

    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments object


@dataclass(frozen=True)
class ToolCall:
    """One tool request from the model, normalised."""

    id: str
    name: str
    arguments: dict | None  # None when the model's arguments could not be parsed
    raw_arguments: str  # exactly what the model produced
    parse_error: str | None = None

    @classmethod
    def from_raw(cls, id: str, name: str, raw: str) -> ToolCall:
        """From arguments given as JSON text (how most chat APIs deliver them)."""
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            return cls(
                id,
                name,
                None,
                raw if isinstance(raw, str) else repr(raw),
                f"arguments are not valid JSON: {e}",
            )
        if not isinstance(value, dict):
            return cls(id, name, None, raw, "arguments are not a JSON object")
        return cls(id, name, value, raw)

    @classmethod
    def from_object(cls, id: str, name: str, arguments: Any) -> ToolCall:
        """From arguments already parsed by the provider (e.g. Anthropic, Ollama native)."""
        raw = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
        if not isinstance(arguments, dict):
            return cls(id, name, None, raw, "arguments are not a JSON object")
        return cls(id, name, arguments, raw)


@dataclass(frozen=True)
class ToolResult:
    """The kernel's answer to one ToolCall."""

    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant" | "tool_results"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    reasoning: str | None = None  # reasoning text a backend chose to return (model output)
    provider_payload: dict | None = None  # opaque; only the backend that made it reads it

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {self.role!r}")


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int
    temperature: float | None = None
    seed: int | None = None
    options: dict = field(default_factory=dict)  # backend-specific knobs (e.g. effort)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ModelRequest:
        return cls(
            model=d["model"],
            system=d["system"],
            messages=tuple(_message_from_dict(m) for m in d["messages"]),
            tools=tuple(ToolSpec(**t) for t in d["tools"]),
            max_output_tokens=d["max_output_tokens"],
            temperature=d.get("temperature"),
            seed=d.get("seed"),
            options=d.get("options") or {},
        )


@dataclass(frozen=True)
class Usage:
    input_tokens: int  # prompt tokens processed (not served from a cache)
    output_tokens: int
    cached_input_tokens: int = 0  # prompt tokens served from a cache
    cache_write_tokens: int = 0  # prompt tokens written to a provider cache (priced)


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str  # one of FINISH_REASONS
    usage: Usage
    model: str  # the model that actually answered, as the backend reports it
    reasoning: str | None = None
    timings: dict | None = None
    provider_finish_reason: str | None = None  # the provider's own word for it
    raw: dict | None = None  # the provider's response, verbatim (journal only)
    provider_payload: dict | None = None  # state the backend needs back next turn
    request_id: str | None = None  # provider request id, if it gives one

    def __post_init__(self):
        if self.finish_reason not in FINISH_REASONS:
            raise ValueError(
                f"finish_reason must be one of {FINISH_REASONS}, got {self.finish_reason!r}"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ModelResponse:
        return cls(
            text=d.get("text", ""),
            tool_calls=tuple(ToolCall(**c) for c in d.get("tool_calls", [])),
            finish_reason=d["finish_reason"],
            usage=Usage(**d["usage"]),
            model=d["model"],
            reasoning=d.get("reasoning"),
            timings=d.get("timings"),
            provider_finish_reason=d.get("provider_finish_reason"),
            raw=d.get("raw"),
            provider_payload=d.get("provider_payload"),
            request_id=d.get("request_id"),
        )


def _message_from_dict(d: dict) -> Message:
    return Message(
        role=d["role"],
        text=d.get("text", ""),
        tool_calls=tuple(ToolCall(**c) for c in d.get("tool_calls", [])),
        tool_results=tuple(ToolResult(**r) for r in d.get("tool_results", [])),
        reasoning=d.get("reasoning"),
        provider_payload=d.get("provider_payload"),
    )


class ModelError(Exception):
    """A backend could not produce a response.

    kind: "transient"   worth retrying (rate limit, overload, dropped connection)
          "permanent"   retrying will not help (bad request, auth, unknown model)
          "unavailable" the backend is not there at all (local server not running)
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        error_type: str | None = None,
    ):
        if kind not in ERROR_KINDS:
            raise ValueError(f"kind must be one of {ERROR_KINDS}")
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.status = status
        self.request_id = request_id
        self.error_type = error_type or kind


class ModelBackend(Protocol):
    """What every backend provides. See src/agentlab/backends/."""

    name: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def wire_request(self, request: ModelRequest) -> dict:
        """The exact body this backend sends for ``request`` (recorded in the journal)."""
        ...

    def describe(self) -> dict:
        """Facts about the backend recorded in run_started."""
        ...

    def prices(self, model: str) -> dict | None:
        """USD per million tokens for budget estimates, or None for a free backend."""
        ...
