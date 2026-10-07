"""Backend for an OpenAI-compatible chat server on THIS machine, primarily llama.cpp's
`llama-server` (docs/LOCAL-LLM.md, D-028). Standard library only (urllib); no SDK.

    ModelRequest --wire_request--> POST {base_url}/v1/chat/completions --> ModelResponse

Mapping (docs/MODEL-GATEWAY.md §4.1):
- system -> first message {"role": "system"}; user -> {"role": "user"}
- assistant turn -> {"role": "assistant", "content": text or null, "tool_calls": [...]};
  each call's arguments are sent back exactly as the model produced them (raw_arguments),
  malformed ones included, so the model sees its own mistake
- tool results -> one {"role": "tool", "tool_call_id", "content"} per result; this format has
  no error flag, so an error result's content is prefixed "ERROR: "
- ToolSpec -> {"type": "function", "function": {name, description, parameters}}
- options.enable_thinking -> chat_template_kwargs.enable_thinking (llama-server); any other
  option is refused rather than silently dropped
- response: content -> text, reasoning_content -> reasoning, tool_calls -> ToolCall (arguments
  parsed safely; a parse failure is kept as parse_error, never executed), finish_reason ->
  closed set, usage + llama-server `timings` -> Usage (cache_n = prompt tokens reused from the
  server's cache)

Safety:
- Only loopback addresses are accepted (localhost, 127.0.0.0/8, ::1). No override: the
  free-first rule is that this backend talks to a model on this machine (D-033).
- Proxy settings from the environment/registry are ignored, so "local" traffic cannot be
  routed off the machine by a proxy.
- The server only produces text. Nothing here executes a tool; the kernel decides.

NOT VERIFIED against a real llama-server yet (nothing is installed). Field names follow the
llama.cpp server README; their exact behaviour is checked in E020/E021.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from agentlab.model_types import Message, ModelError, ModelRequest, ModelResponse, ToolCall, Usage

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
FINISH = {
    "tool_calls": "tool_calls",
    "stop": "stop",
    "length": "length",
    "content_filter": "refusal",
}
TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}  # 503 = llama-server still loading
SUPPORTED_OPTIONS = {"enable_thinking"}
START_HINT = (
    "Start llama-server first, e.g. `llama-server -m <model.gguf> --jinja -c 4096 "
    "-np 1 --host 127.0.0.1 --port 8080 --alias <[model].name>` "
    "(docs/LOCAL-LLM.md §6)."
)


def check_local(base_url: str) -> str:
    """Return the URL without a trailing slash, or raise ValueError if it is not local."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"base_url must be http(s) on the local machine, got {base_url!r}")
    if parts.username or parts.password:
        raise ValueError("base_url must be a local address without credentials")
    host = (parts.hostname or "").lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"refusing non-local model server {base_url!r}: only localhost, 127.0.0.0/8 "
                "or ::1 are allowed (free-first, local-only backend)"
            ) from None
    return base_url.rstrip("/")


def _message_to_wire(m: Message) -> list[dict]:
    if m.role == "user":
        return [{"role": "user", "content": m.text}]
    if m.role == "assistant":
        out: dict = {"role": "assistant", "content": m.text or None}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.raw_arguments},
                }
                for c in m.tool_calls
            ]
        return [out]
    return [
        {
            "role": "tool",
            "tool_call_id": r.call_id,
            "content": f"ERROR: {r.content}" if r.is_error else r.content,
        }
        for r in m.tool_results
    ]


def to_wire(request: ModelRequest) -> dict:
    unknown = set(request.options) - SUPPORTED_OPTIONS
    if unknown:
        raise ValueError(
            f"options not supported by the openai_compat backend: {sorted(unknown)}"
            f"; supported: {sorted(SUPPORTED_OPTIONS)}"
        )
    messages = [{"role": "system", "content": request.system}]
    for m in request.messages:
        messages += _message_to_wire(m)
    body: dict = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_output_tokens,
        "stream": False,
    }
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in request.tools
        ]
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.seed is not None:
        body["seed"] = request.seed
    if "enable_thinking" in request.options:
        body["chat_template_kwargs"] = {"enable_thinking": request.options["enable_thinking"]}
    return body


def _tool_call(raw: dict, fallback_id: str) -> ToolCall:
    fn = raw.get("function") or {}
    call_id = raw.get("id") or fallback_id
    name = fn.get("name") or ""
    args = fn.get("arguments", "")
    if isinstance(args, str):
        return ToolCall.from_raw(call_id, name, args)
    return ToolCall.from_object(call_id, name, args)  # some servers send an object


def response_from_wire(data: dict, call_number: int) -> ModelResponse:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = tuple(
        _tool_call(c, f"call_{call_number}_{i}") for i, c in enumerate(msg.get("tool_calls") or [])
    )
    provider = choice.get("finish_reason")
    finish = FINISH.get(provider, "other")
    if calls and finish == "stop":
        finish = "tool_calls"  # some servers report "stop" on a tool-calling turn
    usage = data.get("usage") or {}
    timings = data.get("timings")
    if timings and "prompt_n" in timings:
        input_tokens, cached = timings.get("prompt_n") or 0, timings.get("cache_n") or 0
    else:
        input_tokens = usage.get("prompt_tokens") or 0
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    output = usage.get("completion_tokens")
    if output is None and timings:
        output = timings.get("predicted_n")
    return ModelResponse(
        text=msg.get("content") or "",
        tool_calls=calls,
        finish_reason=finish,
        usage=Usage(
            input_tokens=input_tokens, output_tokens=output or 0, cached_input_tokens=cached
        ),
        model=data.get("model") or "",
        reasoning=msg.get("reasoning_content"),
        timings=timings,
        provider_finish_reason=provider,
        raw=data,
    )


def _error_message(body: bytes) -> str:
    try:
        err = json.loads(body).get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    except ValueError, AttributeError:
        return body.decode("utf-8", errors="replace")[:500]


class OpenAICompatBackend:
    name = "openai_compat"

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 300.0):
        self.base_url = check_local(base_url)
        self.timeout = timeout
        self.calls = 0
        # An opener with NO proxy handler entries: never route through a proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def wire_request(self, request: ModelRequest) -> dict:
        return to_wire(request)

    def _open(self, req: urllib.request.Request, timeout: float) -> bytes:
        with self._opener.open(req, timeout=timeout) as resp:
            return resp.read()

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        url = f"{self.base_url}/v1/chat/completions"
        body = json.dumps(to_wire(request)).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            raw = self._open(req, self.timeout)
        except urllib.error.HTTPError as e:
            kind = "transient" if e.code in TRANSIENT_STATUS else "permanent"
            raise ModelError(
                kind,
                f"HTTP {e.code} from {url}: {_error_message(e.read())}",
                status=e.code,
                error_type="HTTPError",
            ) from e
        except TimeoutError as e:
            raise ModelError(
                "permanent",
                f"no response from {url} within {self.timeout}s (request_timeout_s); not retried",
                error_type="Timeout",
            ) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise ModelError(
                    "permanent",
                    f"no response from {url} within {self.timeout}s "
                    "(request_timeout_s); not retried",
                    error_type="Timeout",
                ) from e
            raise ModelError(
                "unavailable",
                f"cannot reach the local model server at "
                f"{self.base_url} ({e.reason}). {START_HINT}",
                error_type="Unavailable",
            ) from e
        except ConnectionError as e:
            raise ModelError(
                "unavailable",
                f"cannot reach the local model server at {self.base_url} ({e}). {START_HINT}",
                error_type="Unavailable",
            ) from e
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ModelError(
                "permanent",
                f"server returned non-JSON from {url}: {raw[:200]!r}",
                error_type="BadResponse",
            ) from e
        return response_from_wire(data, self.calls)

    def describe(self) -> dict:
        """Facts about the server, recorded in run_started (best effort: GET /props)."""
        info: dict = {"name": self.name, "base_url": self.base_url}
        try:
            props = json.loads(
                self._open(urllib.request.Request(f"{self.base_url}/props"), timeout=5)
            )
            server = {
                k: props[k] for k in ("model_path", "build_info", "total_slots") if k in props
            }
            n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
            if n_ctx is not None:
                server["n_ctx"] = n_ctx
            if isinstance(props.get("chat_template"), str):
                server["chat_template_sha256"] = hashlib.sha256(
                    props["chat_template"].encode()
                ).hexdigest()
            info["server"] = server
        except (OSError, ValueError) as e:  # URLError is an OSError
            info["server"] = {"props_error": f"{type(e).__name__}: {e}"}
        return info

    def prices(self, model: str) -> dict | None:
        return None  # local inference: no API cost
