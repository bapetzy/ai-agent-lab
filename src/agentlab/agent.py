"""The agent loop. This is the whole mechanism; everything else is bookkeeping.

    messages = [the task]
    loop:
        ask the model (sending ALL messages so far; the model remembers nothing)
        append its answer to messages
        if it asked for tools:
            let the kernel handle each request (policy, approval, execution, journal)
            append all tool results as ONE user message
            go round again
        otherwise:
            stop; its text is the final answer

The model never executes anything. It only *asks*, by returning ``tool_use`` blocks.
The budget is checked before every model call so a confused agent cannot loop forever.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentlab.budget import Budget
from agentlab.gateway import ModelGateway
from agentlab.journal import Recorder
from agentlab.kernel import Kernel


@dataclass(frozen=True)
class AgentOutcome:
    status: str  # "completed" (the agent stopped by itself) or "failed"
    reason: str | None
    final_text: str


def _text_of(response: dict) -> str:
    return "".join(b.get("text", "") for b in response["content"] if b["type"] == "text")


def _stop(rec: Recorder, budget: Budget, why: str) -> AgentOutcome:
    rec.emit("limit_reached", {"limit": why, "usage": budget.snapshot()})
    return AgentOutcome("failed", f"limit: {why}", "")


def run_agent(
    *,
    gateway: ModelGateway,
    kernel: Kernel,
    budget: Budget,
    rec: Recorder,
    model: str,
    system: str,
    tools: list[dict],
    prompt: str,
    effort: str | None = None,
) -> AgentOutcome:
    messages: list[dict] = [{"role": "user", "content": prompt}]

    while True:
        # 1. May we call the model again?
        if (why := budget.check_before_model_call()) is not None:
            return _stop(rec, budget, why)

        # 2. Send the whole conversation; get the next assistant message.
        request = {
            "model": model,
            "max_tokens": budget.limits.max_tokens_per_response,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if effort:
            request["output_config"] = {"effort": effort}
        response = gateway.call(request)
        budget.record_model_call(response["usage"])
        messages.append({"role": "assistant", "content": response["content"]})
        if (why := budget.exceeded()) is not None:
            return _stop(rec, budget, why)

        # 3. Tool requests: the kernel decides and executes; results go back to the model.
        if response["stop_reason"] == "tool_use":
            calls = [b for b in response["content"] if b["type"] == "tool_use"]
            if (why := budget.check_before_tool_calls(len(calls))) is not None:
                return _stop(rec, budget, why)
            budget.record_tool_calls(len(calls))
            results = [kernel.handle(call) for call in calls]
            messages.append({"role": "user", "content": results})
            continue

        # 4. No tool request: the agent has stopped.
        if response["stop_reason"] == "end_turn":
            return AgentOutcome("completed", None, _text_of(response))
        # max_tokens, refusal, or anything unexpected: stop and say why.
        return AgentOutcome("failed", f"stop_reason:{response['stop_reason']}", _text_of(response))
