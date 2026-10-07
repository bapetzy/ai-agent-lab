"""Inspection must keep working for runs recorded before the provider-neutral refactor."""

from agentlab.journal import Journal
from agentlab.trace import format_trace, summarize


def phase1_run(journal: Journal) -> str:
    """A minimal schema-v1 (Phase 1, Anthropic-shaped) run, as it was journaled."""
    r = "v1-run"

    def add(type_, payload):
        journal.append(r, type_, payload, agent_id="a", task_id="t")

    add(
        "run_started",
        {
            "task": {"name": "old"},
            "model_requested": "claude-opus-5-5",
            "model_backend": "anthropic",
            "fallback": "disabled",
            "tools": [{"name": "read_file", "input_schema": {}}],
            "policy": [],
        },
    )
    add(
        "model_request",
        {
            "turn": 1,
            "n_messages": 1,
            "tools": ["read_file"],
            "max_tokens": 8000,
            "output_config": {"effort": "medium"},
        },
    )
    add(
        "model_response",
        {
            "turn": 1,
            "stop_reason": "tool_use",
            "actual_model": "claude-opus-5-5",
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "text": "",
            "tool_uses": [{"id": "x", "name": "read_file", "input": {"path": "a"}}],
        },
    )
    add("tool_requested", {"tool_use_id": "x", "tool": "read_file", "input": {"path": "a"}})
    add(
        "run_completed",
        {
            "success": True,
            "final_text": "done",
            "usage": {"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.0},
        },
    )
    return r


def test_trace_and_summary_still_read_schema_v1_runs():
    journal = Journal(":memory:")
    run_id = phase1_run(journal)
    text = format_trace(journal, run_id)
    assert "read_file" in text and "tool_use" in text and "COMPLETED" in text
    s = summarize(journal, run_id)
    assert s["schema_version"] == 1 and s["backend"] == "anthropic" and s["tool_calls"] == 1
