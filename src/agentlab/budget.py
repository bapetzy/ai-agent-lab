"""Budgets: make runaway runs hard.

Every limit is checked by the kernel. Hitting one ends the run with a clear
``limit_reached`` event. Cost is an *estimate* from list prices and the usage numbers the
API reports; it is not a bill.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# USD per million tokens, from Anthropic's published list prices (claude-api skill,
# cached 2026-09-25). cache_write = 5-minute cache writes. Re-check before trusting.
PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}


@dataclass(frozen=True)
class Limits:
    max_turns: int = 10  # model calls per run
    max_tool_calls: int = 30
    max_input_tokens: int = 300_000  # summed over all model calls (incl. cache reads)
    max_output_tokens: int = 40_000  # summed over all model calls (incl. thinking)
    max_cost_usd: float = 0.50
    max_tokens_per_response: int = 8_000  # sent to the API as max_tokens

    def to_dict(self) -> dict:
        return asdict(self)


class Budget:
    def __init__(self, limits: Limits, *, model: str, prices: dict[str, float] | None = None):
        if prices is None:
            if model not in PRICES_PER_MTOK:
                raise ValueError(
                    f"no price known for model {model!r}; add it to PRICES_PER_MTOK "
                    "so the spend limit can be enforced"
                )
            prices = PRICES_PER_MTOK[model]
        self.limits = limits
        self.prices = {"cache_read": 0.0, "cache_write": 0.0, **prices}
        self.turns = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0

    def record_model_call(self, usage: dict) -> None:
        plain = usage.get("input_tokens") or 0
        read = usage.get("cache_read_input_tokens") or 0
        write = usage.get("cache_creation_input_tokens") or 0
        out = usage.get("output_tokens") or 0
        self.turns += 1
        self.input_tokens += plain + read + write
        self.output_tokens += out
        p = self.prices
        self.cost_usd += (
            plain * p["input"]
            + read * p["cache_read"]
            + write * p["cache_write"]
            + out * p["output"]
        ) / 1_000_000

    def record_tool_calls(self, n: int) -> None:
        self.tool_calls += n

    def exceeded(self) -> str | None:
        """A limit already crossed by recorded usage, or None."""
        lim = self.limits
        if self.input_tokens > lim.max_input_tokens:
            return f"max_input_tokens exceeded ({self.input_tokens} > {lim.max_input_tokens})"
        if self.output_tokens > lim.max_output_tokens:
            return f"max_output_tokens exceeded ({self.output_tokens} > {lim.max_output_tokens})"
        if self.cost_usd > lim.max_cost_usd:
            return f"max_cost_usd exceeded (${self.cost_usd:.4f} > ${lim.max_cost_usd})"
        return None

    def check_before_model_call(self) -> str | None:
        """Why the next model call must not happen, or None if it may."""
        lim = self.limits
        if self.turns >= lim.max_turns:
            return f"max_turns reached ({lim.max_turns})"
        if (already := self.exceeded()) is not None:
            return already
        worst_case = self.cost_usd + lim.max_tokens_per_response * self.prices["output"] / 1e6
        if worst_case > lim.max_cost_usd:
            return (
                f"max_cost_usd would be exceeded: spent ${self.cost_usd:.4f}, next call may "
                f"add up to ${worst_case - self.cost_usd:.4f} of output, cap ${lim.max_cost_usd}"
            )
        return None

    def check_before_tool_calls(self, n: int) -> str | None:
        if self.tool_calls + n > self.limits.max_tool_calls:
            return f"max_tool_calls reached ({self.limits.max_tool_calls})"
        return None

    def snapshot(self) -> dict:
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }
