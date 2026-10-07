import pytest

from agentlab.budget import Budget, Limits


def test_turn_limit_blocks_the_next_model_call():
    b = Budget(Limits(max_turns=2), model="claude-opus-5-5")
    assert b.check_before_model_call() is None
    b.record_model_call({"input_tokens": 10, "output_tokens": 5})
    b.record_model_call({"input_tokens": 10, "output_tokens": 5})
    assert "max_turns" in b.check_before_model_call()


def test_cost_uses_list_prices_including_cache_tokens():
    b = Budget(Limits(), model="claude-opus-5-5")
    b.record_model_call(
        {
            "input_tokens": 1_000_000,
            "output_tokens": 100_000,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 0,
        }
    )
    # 1M * $4 + 0.1M * $20 + 1M * $0.20
    assert b.cost_usd == pytest.approx(4.0 + 2.0 + 0.20)
    assert b.input_tokens == 2_000_000


def test_spend_limit_counts_worst_case_output_of_next_call():
    # $0.10 cap; next call could emit 8000 output tokens = $0.16 on Opus 5.5
    b = Budget(Limits(max_cost_usd=0.10, max_tokens_per_response=8000), model="claude-opus-5-5")
    assert "max_cost_usd" in b.check_before_model_call()


def test_token_limits():
    b = Budget(Limits(max_input_tokens=100), model="claude-opus-5-5")
    b.record_model_call({"input_tokens": 150, "output_tokens": 1})
    assert "max_input_tokens" in b.exceeded()


def test_tool_call_limit():
    b = Budget(Limits(max_tool_calls=2), model="claude-opus-5-5")
    assert b.check_before_tool_calls(2) is None
    assert "max_tool_calls" in b.check_before_tool_calls(3)


def test_unknown_model_without_price_is_refused():
    with pytest.raises(ValueError, match="no price"):
        Budget(Limits(), model="claude-mystery-9")


def test_explicit_prices_allow_fake_models():
    b = Budget(Limits(), model="scripted-fake", prices={"input": 0, "output": 0})
    b.record_model_call({"input_tokens": 5, "output_tokens": 5})
    assert b.cost_usd == 0
