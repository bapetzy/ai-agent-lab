"""The agent loop. This is the whole mechanism; everything else is bookkeeping.

    messages = [the task]
    loop:
        ask the model (sending ALL messages so far; the model remembers nothing)
        append its answer to messages
        if it proposed tool calls:
            let the kernel handle each one (policy, approval, execution, journal)
            append all results as ONE message
            go round again
        otherwise:
            stop; its text is the final answer

The loop speaks only the kernel's own types (model_types.py): it does not know whether
the answer came from a scripted fake, a local model, or an external API. The model never
executes anything; it only proposes. Budgets are checked before every model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentlab.budget import Budget
from agentlab.gateway import ModelGateway
from agentlab.journal import Recorder
from agentlab.kernel import Kernel
from agentlab.model_types import Message, ModelRequest, ToolSpec


@dataclass(frozen=True)
class AgentOutcome:
    status: str  # "completed" (the agent stopped by itself) or "failed"
    reason: str | None
    final_text: str


def _stop(rec: Recorder, budget: Budget, why: str) -> AgentOutcome:
    rec.emit("limit_reached", {"limit": why, "usage": budget.snapshot()})
    return AgentOutcome("failed", f"limit: {why}", "")


def run_agent(
    *,
    gateway: ModelGateway,
    kernel: Kernel,
    budget: Budget,
    rec: Recorder,
    model: dict,  # the task's [model] section: name, temperature, seed, options
    system: str,
    tools: tuple[ToolSpec, ...],
    prompt: str,
) -> AgentOutcome:
    messages: list[Message] = [Message("user", text=prompt)]

    while True:
        # 1. May we call the model again?
        if (why := budget.check_before_model_call()) is not None:
            return _stop(rec, budget, why)

        # 2. Send the whole conversation; get the next assistant turn.
        response = gateway.call(
            ModelRequest(
                model=model["name"],
                system=system,
                messages=tuple(messages),
                tools=tools,
                max_output_tokens=budget.limits.max_tokens_per_response,
                temperature=model.get("temperature"),
                seed=model.get("seed"),
                options=model.get("options") or {},
            )
        )
        budget.record_model_call(response.usage)
        messages.append(
            Message(
                "assistant",
                text=response.text,
                tool_calls=response.tool_calls,
                reasoning=response.reasoning,
                provider_payload=response.provider_payload,
            )
        )
        if (why := budget.exceeded()) is not None:
            return _stop(rec, budget, why)

        # 3. Tool calls: the kernel decides and executes; results go back to the model.
        #    Only a response that finished *because* it asked for tools is acted on; calls in
        #    a response cut off by the length limit may be incomplete and are never executed.
        if response.finish_reason == "tool_calls" and response.tool_calls:
            if (why := budget.check_before_tool_calls(len(response.tool_calls))) is not None:
                return _stop(rec, budget, why)
            budget.record_tool_calls(len(response.tool_calls))
            results = tuple(kernel.handle(call) for call in response.tool_calls)
            messages.append(Message("tool_results", tool_results=results))
            continue

        # 4. No tool request: the agent has stopped.
        if response.finish_reason == "stop":
            return AgentOutcome("completed", None, response.text)
        return AgentOutcome("failed", f"finish_reason:{response.finish_reason}", response.text)
