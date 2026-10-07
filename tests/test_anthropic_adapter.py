"""The optional Anthropic adapter: pure mapping functions, tested without the SDK installed.
All Anthropic wire shapes live in agentlab/backends/anthropic.py and nowhere else."""

from agentlab.backends.anthropic import AnthropicBackend, response_from_wire, to_wire
from agentlab.model_types import Message, ModelRequest, ToolCall, ToolResult, ToolSpec


def request():
    return ModelRequest(
        model="claude-opus-5-5",
        system="sys",
        messages=(
            Message("user", text="read a.md"),
            Message(
                "assistant",
                text="",
                tool_calls=(ToolCall.from_object("toolu_1", "read_file", {"path": "a.md"}),),
                provider_payload={
                    "content": [
                        {"type": "thinking", "thinking": "", "signature": "SIG"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "a.md"},
                        },
                    ]
                },
            ),
            Message(
                "tool_results", tool_results=(ToolResult("toolu_1", "read_file", "hello", False),)
            ),
        ),
        tools=(ToolSpec("read_file", "Read a file", {"type": "object", "properties": {}}),),
        max_output_tokens=1000,
        options={"effort": "medium"},
    )


def test_request_maps_to_messages_api_shape():
    wire = to_wire(request())
    assert wire["system"] == "sys"
    assert wire["max_tokens"] == 1000
    assert wire["output_config"] == {"effort": "medium"}
    assert wire["tools"][0] == {
        "name": "read_file",
        "description": "Read a file",
        "input_schema": {"type": "object", "properties": {}},
        "strict": True,
    }
    assert "temperature" not in wire  # Opus 5.5 rejects sampling params; only sent if set


def test_thinking_blocks_round_trip_verbatim_via_provider_payload():
    wire = to_wire(request())
    assistant = wire["messages"][1]
    assert assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": "SIG"}


def test_tool_results_become_a_user_message_of_tool_result_blocks():
    block = to_wire(request())["messages"][2]["content"][0]
    assert block == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "hello",
        "is_error": False,
    }


def test_response_maps_to_neutral_types():
    data = {
        "id": "msg_1",
        "model": "claude-opus-5-5",
        "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "S"},
            {"type": "text", "text": "Reading."},
            {"type": "tool_use", "id": "toolu_9", "name": "read_file", "input": {"path": "x"}},
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 7,
            "cache_creation_input_tokens": 2,
        },
    }
    r = response_from_wire(data, request_id="req_1")
    assert r.finish_reason == "tool_calls" and r.provider_finish_reason == "tool_use"
    assert r.text == "Reading."
    assert r.tool_calls[0].id == "toolu_9" and r.tool_calls[0].arguments == {"path": "x"}
    assert (r.usage.input_tokens, r.usage.cached_input_tokens, r.usage.cache_write_tokens) == (
        10,
        7,
        2,
    )
    assert r.provider_payload == {"content": data["content"]}
    assert r.request_id == "req_1"


def test_stop_reasons_map_to_the_closed_set():
    for wire, neutral in [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("refusal", "refusal"),
        ("pause_turn", "other"),
    ]:
        r = response_from_wire(
            {
                "model": "m",
                "stop_reason": wire,
                "content": [],
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )
        assert r.finish_reason == neutral


def test_adapter_is_priced_and_refuses_unknown_models():
    import pytest

    b = AnthropicBackend(api_key="unused", client=object())
    assert b.prices("claude-opus-5-5")["input"] == 4.00
    with pytest.raises(ValueError, match="no price"):
        b.prices("claude-mystery-9")
