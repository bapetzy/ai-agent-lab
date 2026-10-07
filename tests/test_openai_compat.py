"""Step 7: the OpenAI-compatible backend (for a local llama.cpp `llama-server`), tested against
an in-process stub HTTP server. No llama.cpp, no model, no internet."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentlab.backends.openai_compat import OpenAICompatBackend
from agentlab.model_types import Message, ModelError, ModelRequest, ToolCall, ToolResult, ToolSpec

MODEL = "qwen3-1.7b-q4_k_m"


class Stub:
    """A tiny fake llama-server. Queue responses; inspect what the backend sent."""

    def __init__(self):
        self.replies: list[tuple[int, object]] = []  # (status, body dict or raw str)
        self.requests: list[dict] = []
        self.props = {
            "model_path": "D:/agentlab/models/x.gguf",
            "build_info": "b11461",
            "default_generation_settings": {"n_ctx": 4096},
            "total_slots": 1,
        }
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/props":
                    self._send(200, stub.props)
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                stub.requests.append({"path": self.path, "body": body})
                status, reply = stub.replies.pop(0)
                self._send(status, reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def completion(message: dict, finish="stop", usage=None, timings=None, model=MODEL) -> dict:
    out = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}
        ],
        "usage": usage or {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57},
    }
    if timings is not None:
        out["timings"] = timings
    return out


def request(*messages, tools=(), options=None, temperature=0.0, seed=1) -> ModelRequest:
    return ModelRequest(
        model=MODEL,
        system="You are a test agent.",
        messages=tuple(messages) or (Message("user", text="hi"),),
        tools=tools,
        max_output_tokens=256,
        temperature=temperature,
        seed=seed,
        options=options or {},
    )


READ = ToolSpec(
    "read_file",
    "Read a file.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)


def test_normal_text_response(stub):
    stub.replies.append((200, completion({"content": "Hello."})))
    r = OpenAICompatBackend(base_url=stub.url).complete(request())
    assert r.text == "Hello."
    assert r.tool_calls == ()
    assert r.finish_reason == "stop" and r.provider_finish_reason == "stop"
    assert r.model == MODEL
    sent = stub.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "You are a test agent."}
    assert sent["body"]["messages"][1] == {"role": "user", "content": "hi"}
    assert sent["body"]["max_tokens"] == 256
    assert sent["body"]["temperature"] == 0.0 and sent["body"]["seed"] == 1
    assert sent["body"]["stream"] is False


def test_tools_are_sent_in_openai_function_format(stub):
    stub.replies.append((200, completion({"content": "ok"})))
    OpenAICompatBackend(base_url=stub.url).complete(request(tools=(READ,)))
    assert stub.requests[0]["body"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file.",
                "parameters": READ.parameters,
            },
        }
    ]


def test_tool_call_is_normalised(stub):
    stub.replies.append(
        (
            200,
            completion(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "notes/a.md"}',
                            },
                        }
                    ],
                },
                finish="tool_calls",
            ),
        )
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request(tools=(READ,)))
    assert r.finish_reason == "tool_calls"
    assert r.text == ""
    (call,) = r.tool_calls
    assert (call.id, call.name, call.arguments) == ("call_abc", "read_file", {"path": "notes/a.md"})
    assert call.parse_error is None


def test_tool_call_without_id_gets_a_deterministic_id(stub):
    stub.replies.append(
        (
            200,
            completion(
                {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                        }
                    ]
                },
                finish="tool_calls",
            ),
        )
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request(tools=(READ,)))
    assert r.tool_calls[0].id == "call_1_0"


def test_arguments_given_as_an_object_are_accepted(stub):
    stub.replies.append(
        (
            200,
            completion(
                {
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": {"path": "a"}},
                        }
                    ]
                },
                finish="tool_calls",
            ),
        )
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request(tools=(READ,)))
    assert r.tool_calls[0].arguments == {"path": "a"}


def test_malformed_tool_arguments_are_preserved_not_executed(stub):
    stub.replies.append(
        (
            200,
            completion(
                {
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a'},
                        }
                    ]
                },
                finish="tool_calls",
            ),
        )
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request(tools=(READ,)))
    (call,) = r.tool_calls
    assert call.arguments is None
    assert call.raw_arguments == '{"path": "a'
    assert "not valid JSON" in call.parse_error


def test_tool_result_round_trip(stub):
    stub.replies.append((200, completion({"content": "done"})))
    call = ToolCall.from_raw("call_abc", "read_file", '{"path": "notes/a.md"}')
    history = (
        Message("user", text="read it"),
        Message("assistant", text="Reading.", tool_calls=(call,)),
        Message(
            "tool_results",
            tool_results=(ToolResult("call_abc", "read_file", "file text", is_error=False),),
        ),
        Message(
            "assistant",
            text="",
            tool_calls=(ToolCall.from_raw("call_x", "read_file", '{"path": "b"}'),),
        ),
        Message(
            "tool_results",
            tool_results=(
                ToolResult("call_x", "read_file", '{"error": "not found"}', is_error=True),
            ),
        ),
    )
    OpenAICompatBackend(base_url=stub.url).complete(request(*history, tools=(READ,)))
    msgs = stub.requests[0]["body"]["messages"]
    assert msgs[2] == {
        "role": "assistant",
        "content": "Reading.",
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "notes/a.md"}'},
            }
        ],
    }
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_abc", "content": "file text"}
    assert msgs[4]["content"] is None  # no text: null content, as the format expects
    assert msgs[5] == {
        "role": "tool",
        "tool_call_id": "call_x",
        "content": 'ERROR: {"error": "not found"}',
    }


def test_malformed_call_is_sent_back_verbatim_in_history(stub):
    stub.replies.append((200, completion({"content": "sorry"})))
    bad = ToolCall.from_raw("c1", "read_file", '{"path": ')
    history = (
        Message("user", text="x"),
        Message("assistant", tool_calls=(bad,)),
        Message(
            "tool_results",
            tool_results=(ToolResult("c1", "read_file", "invalid_input", is_error=True),),
        ),
    )
    OpenAICompatBackend(base_url=stub.url).complete(request(*history, tools=(READ,)))
    sent_call = stub.requests[0]["body"]["messages"][2]["tool_calls"][0]
    assert sent_call["function"]["arguments"] == '{"path": '


@pytest.mark.parametrize(
    "provider, has_calls, expected",
    [
        ("stop", False, "stop"),
        ("tool_calls", True, "tool_calls"),
        ("stop", True, "tool_calls"),  # some servers say "stop" even when calling tools
        ("length", False, "length"),
        ("length", True, "length"),  # cut off: calls may be incomplete, never tool_calls
        ("content_filter", False, "refusal"),
        ("something_new", False, "other"),
    ],
)
def test_finish_reasons_map_to_the_closed_set(stub, provider, has_calls, expected):
    message = {"content": "x"}
    if has_calls:
        message["tool_calls"] = [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        ]
    stub.replies.append((200, completion(message, finish=provider)))
    r = OpenAICompatBackend(base_url=stub.url).complete(request())
    assert r.finish_reason == expected
    assert r.provider_finish_reason == provider


def test_usage_and_llama_server_timings_are_mapped(stub):
    timings = {
        "cache_n": 40,
        "prompt_n": 10,
        "prompt_ms": 812.5,
        "predicted_n": 7,
        "predicted_ms": 600.0,
        "predicted_per_second": 11.7,
    }
    stub.replies.append(
        (200, completion({"content": "x", "reasoning_content": "thinking..."}, timings=timings))
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request())
    assert (r.usage.input_tokens, r.usage.cached_input_tokens, r.usage.output_tokens) == (10, 40, 7)
    assert r.timings == timings
    assert r.reasoning == "thinking..."
    assert r.raw["id"] == "chatcmpl-1"


def test_usage_without_timings_falls_back_to_usage_block(stub):
    stub.replies.append(
        (200, completion({"content": "x"}, usage={"prompt_tokens": 50, "completion_tokens": 7}))
    )
    r = OpenAICompatBackend(base_url=stub.url).complete(request())
    assert (r.usage.input_tokens, r.usage.cached_input_tokens, r.usage.output_tokens) == (50, 0, 7)
    assert r.timings is None


def test_enable_thinking_option_maps_to_chat_template_kwargs(stub):
    stub.replies.append((200, completion({"content": "x"})))
    OpenAICompatBackend(base_url=stub.url).complete(request(options={"enable_thinking": False}))
    assert stub.requests[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_unknown_options_are_refused_not_silently_dropped(stub):
    with pytest.raises(ValueError, match="effort"):
        OpenAICompatBackend(base_url=stub.url).wire_request(request(options={"effort": "high"}))


def test_backend_is_free_and_describes_the_server(stub):
    b = OpenAICompatBackend(base_url=stub.url)
    assert b.prices(MODEL) is None
    d = b.describe()
    assert d["name"] == "openai_compat" and d["base_url"] == stub.url
    assert d["server"]["model_path"] == "D:/agentlab/models/x.gguf"
    assert d["server"]["build_info"] == "b11461"


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:8080",
        "http://example.com:8080",
        "http://0.0.0.0:8080",
        "http://10.0.0.1",
        "https://api.openai.com",
        "http://user:pw@127.0.0.1:8080",
        "ftp://127.0.0.1:8080",
        "http://127.0.0.1.example.com:8080",
    ],
)
def test_non_local_server_addresses_are_refused(url):
    with pytest.raises(ValueError, match="local"):
        OpenAICompatBackend(base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://[::1]:8080",
        "http://127.0.0.2:9000",
    ],
)
def test_loopback_addresses_are_allowed(url):
    assert OpenAICompatBackend(base_url=url).base_url == url


def dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_unavailable_local_server_fails_clearly():
    b = OpenAICompatBackend(base_url=f"http://127.0.0.1:{dead_port()}")
    with pytest.raises(ModelError) as e:
        b.complete(request())
    assert e.value.kind == "unavailable"
    assert "llama-server" in e.value.message
    assert "props_error" in b.describe()["server"]


def test_server_errors_are_classified(stub):
    stub.replies.append((503, {"error": {"code": 503, "message": "Loading model"}}))
    stub.replies.append((400, {"error": {"code": 400, "message": "bad request"}}))
    stub.replies.append((200, "this is not json"))
    b = OpenAICompatBackend(base_url=stub.url)
    with pytest.raises(ModelError) as e1:
        b.complete(request())
    assert e1.value.kind == "transient" and e1.value.status == 503
    with pytest.raises(ModelError) as e2:
        b.complete(request())
    assert e2.value.kind == "permanent" and "bad request" in e2.value.message
    with pytest.raises(ModelError) as e3:
        b.complete(request())
    assert e3.value.kind == "permanent" and "JSON" in e3.value.message


def test_environment_proxies_are_never_used(stub, monkeypatch):
    """A proxy could carry 'local' traffic off the machine; the backend ignores proxy settings."""
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:9")
    monkeypatch.setenv("http_proxy", "http://203.0.113.1:9")
    stub.replies.append((200, completion({"content": "direct"})))
    assert OpenAICompatBackend(base_url=stub.url).complete(request()).text == "direct"
