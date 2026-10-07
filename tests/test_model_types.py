"""Step 1: the provider-neutral types the kernel speaks."""

import pytest

from agentlab.model_types import (
    FINISH_REASONS,
    Message,
    ModelError,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)


def test_tool_call_parses_valid_json_arguments():
    call = ToolCall.from_raw("c1", "read_file", '{"path": "a.md"}')
    assert call.arguments == {"path": "a.md"}
    assert call.parse_error is None
    assert call.raw_arguments == '{"path": "a.md"}'


@pytest.mark.parametrize(
    "raw, problem",
    [
        ('{"path": "a.md"', "not valid JSON"),
        ("[1, 2]", "not a JSON object"),
        ("", "not valid JSON"),
    ],
)
def test_malformed_arguments_are_preserved_not_hidden(raw, problem):
    call = ToolCall.from_raw("c1", "read_file", raw)
    assert call.arguments is None
    assert problem in call.parse_error
    assert call.raw_arguments == raw  # exactly what the model produced


def test_tool_call_from_object_arguments_keeps_a_raw_form():
    call = ToolCall.from_object("c2", "list_files", {"path": "."})
    assert call.arguments == {"path": "."}
    assert call.raw_arguments == '{"path":"."}'


def test_request_and_response_round_trip_through_plain_dicts():
    req = ModelRequest(
        model="m",
        system="sys",
        messages=(
            Message("user", text="hi"),
            Message(
                "assistant",
                text="",
                tool_calls=(ToolCall.from_object("c1", "t", {}),),
                provider_payload={"opaque": [1]},
            ),
            Message("tool_results", tool_results=(ToolResult("c1", "t", "ok", False),)),
        ),
        tools=(ToolSpec("t", "a tool", {"type": "object", "properties": {}}),),
        max_output_tokens=100,
        temperature=0.0,
        seed=1,
        options={"effort": "low"},
    )
    assert ModelRequest.from_dict(req.to_dict()) == req

    resp = ModelResponse(
        text="done",
        tool_calls=(ToolCall.from_raw("c9", "t", "{bad"),),
        finish_reason="tool_calls",
        usage=Usage(input_tokens=5, output_tokens=2, cached_input_tokens=3),
        model="m",
        reasoning="because",
        timings={"prompt_ms": 1.5},
        provider_finish_reason="tool_use",
        raw={"anything": True},
    )
    assert ModelResponse.from_dict(resp.to_dict()) == resp


def test_finish_reason_must_be_one_of_the_closed_set():
    assert set(FINISH_REASONS) == {"tool_calls", "stop", "length", "refusal", "other"}
    with pytest.raises(ValueError, match="finish_reason"):
        ModelResponse(
            text="", tool_calls=(), finish_reason="end_turn", usage=Usage(0, 0), model="m"
        )


def test_message_roles_are_checked():
    with pytest.raises(ValueError, match="role"):
        Message("system", text="x")


def test_model_error_kinds():
    e = ModelError("transient", "rate limited", status=429, error_type="RateLimitError")
    assert e.kind == "transient" and e.status == 429
    with pytest.raises(ValueError):
        ModelError("sometimes", "x")
