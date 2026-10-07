"""Budgets: make runaway runs hard, for free and paid backends alike.

Every limit is checked by the kernel. Hitting one ends the run with a clear
``limit_reached`` event.

- Turns, tool calls and token totals apply to every backend.
- Wall-clock time applies to every backend. For a local model on a CPU, time is the real
  cost, so this is the main guard against a confused agent looping for an hour.
- The spend cap applies only when the backend has prices (an optional paid API). Cost is an
  *estimate* from list prices and reported usage, not a bill. Free backends report None.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from agentlab.model_types import Usage


@dataclass(frozen=True)
class Limits:
    max_turns: int = 10  # model calls per run
    max_tool_calls: int = 30
    max_input_tokens: int = 300_000  # summed over all model calls (incl. cached)
    max_output_tokens: int = 40_000  # summed over all model calls
    max_cost_usd: float = 0.50  # only enforced for priced backends
    max_tokens_per_response: int = 8_000  # sent to the backend as max_output_tokens
    max_wall_seconds: float = 900.0  # whole run
    request_timeout_s: float = 300.0  # one model call (used by network backends)

    def to_dict(self) -> dict:
        return asdict(self)


class Budget:
    def __init__(self, limits: Limits, *, prices: dict[str, float] | None, clock=time.monotonic):
        self.limits = limits
        self.prices = None if prices is None else {"cache_read": 0.0, "cache_write": 0.0, **prices}
        self.clock = clock
        self.started = clock()
        self.turns = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.cost_usd: float | None = None if prices is None else 0.0

    def record_model_call(self, usage: Usage) -> None:
        self.turns += 1
        self.input_tokens += (
            usage.input_tokens + usage.cached_input_tokens + usage.cache_write_tokens
        )
        self.cached_input_tokens += usage.cached_input_tokens
        self.output_tokens += usage.output_tokens
        if self.prices is not None:
            p = self.prices
            self.cost_usd += (
                usage.input_tokens * p["input"]
                + usage.cached_input_tokens * p["cache_read"]
                + usage.cache_write_tokens * p["cache_write"]
                + usage.output_tokens * p["output"]
            ) / 1_000_000

    def record_tool_calls(self, n: int) -> None:
        self.tool_calls += n

    def elapsed(self) -> float:
        return self.clock() - self.started

    def _out_of_time(self) -> str | None:
        if (spent := self.elapsed()) > self.limits.max_wall_seconds:
            return f"max_wall_seconds exceeded ({spent:.1f}s > {self.limits.max_wall_seconds}s)"
        return None

    def exceeded(self) -> str | None:
        """A limit already crossed by recorded usage, or None."""
        lim = self.limits
        if self.input_tokens > lim.max_input_tokens:
            return f"max_input_tokens exceeded ({self.input_tokens} > {lim.max_input_tokens})"
        if self.output_tokens > lim.max_output_tokens:
            return f"max_output_tokens exceeded ({self.output_tokens} > {lim.max_output_tokens})"
        if self.cost_usd is not None and self.cost_usd > lim.max_cost_usd:
            return f"max_cost_usd exceeded (${self.cost_usd:.4f} > ${lim.max_cost_usd})"
        return self._out_of_time()

    def check_before_model_call(self) -> str | None:
        """Why the next model call must not happen, or None if it may."""
        lim = self.limits
        if self.turns >= lim.max_turns:
            return f"max_turns reached ({lim.max_turns})"
        if (already := self.exceeded()) is not None:
            return already
        if self.prices is not None:
            worst = self.cost_usd + lim.max_tokens_per_response * self.prices["output"] / 1e6
            if worst > lim.max_cost_usd:
                return (
                    f"max_cost_usd would be exceeded: spent ${self.cost_usd:.4f}, next call "
                    f"may add up to ${worst - self.cost_usd:.4f}, cap ${lim.max_cost_usd}"
                )
        return None

    def check_before_tool_calls(self, n: int) -> str | None:
        if self.tool_calls + n > self.limits.max_tool_calls:
            return f"max_tool_calls reached ({self.limits.max_tool_calls})"
        return self._out_of_time()

    def snapshot(self) -> dict:
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed(), 3),
        }
