"""Step 5: budgets work for free and priced backends alike."""

import pytest

from agentlab.budget import Budget, Limits
from agentlab.model_types import Usage

OPUS = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_turn_limit_blocks_the_next_model_call():
    b = Budget(Limits(max_turns=2), prices=None)
    assert b.check_before_model_call() is None
    b.record_model_call(Usage(10, 5))
    b.record_model_call(Usage(10, 5))
    assert "max_turns" in b.check_before_model_call()


def test_free_backend_has_no_cost_and_no_spend_cap():
    lim = Limits(max_cost_usd=0.0, max_input_tokens=10**9, max_output_tokens=10**9)
    b = Budget(lim, prices=None)
    b.record_model_call(Usage(1_000_000, 100_000))
    assert b.cost_usd is None
    assert b.snapshot()["cost_usd"] is None
    assert b.check_before_model_call() is None  # tokens/turns would stop it, not money


def test_priced_backend_cost_includes_cached_and_cache_write_tokens():
    b = Budget(Limits(), prices=OPUS)
    b.record_model_call(
        Usage(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cached_input_tokens=1_000_000,
            cache_write_tokens=0,
        )
    )
    assert b.cost_usd == pytest.approx(4.0 + 2.0 + 0.20)
    assert b.input_tokens == 2_000_000


def test_spend_limit_counts_worst_case_output_of_next_call():
    b = Budget(Limits(max_cost_usd=0.10, max_tokens_per_response=8000), prices=OPUS)
    assert "max_cost_usd" in b.check_before_model_call()


def test_token_limits():
    b = Budget(Limits(max_input_tokens=100), prices=None)
    b.record_model_call(Usage(150, 1))
    assert "max_input_tokens" in b.exceeded()


def test_tool_call_limit():
    b = Budget(Limits(max_tool_calls=2), prices=None)
    assert b.check_before_tool_calls(2) is None
    assert "max_tool_calls" in b.check_before_tool_calls(3)


def test_wall_clock_limit_is_the_runaway_guard_for_slow_local_models():
    clock = FakeClock()
    b = Budget(Limits(max_wall_seconds=60), prices=None, clock=clock)
    assert b.check_before_model_call() is None
    clock.t = 61
    assert "max_wall_seconds" in b.check_before_model_call()
    assert "max_wall_seconds" in b.check_before_tool_calls(1)
