import pytest

from quantproof.agent.costs import PRICES, Usage, estimate_cost, price_for


def test_day1_extraction_call_cost():
    # Day 1 go/no-go: 10,667 in, 8,673 out on Muse Spark ($1.25 / $4.25 per million).
    # 10,667 x 1.25e-6 + 8,673 x 4.25e-6 = 0.013334 + 0.036860 = 0.050194.
    usage = Usage(input_tokens=10_667, output_tokens=8_673)
    assert estimate_cost("muse-spark-1.3", usage) == pytest.approx(0.050194, abs=1e-6)


def test_cached_tokens_charged_at_cached_price():
    # 1M input of which 600k cached: 400k x 1.25 + 600k x 0.15 = 0.50 + 0.09 = $0.59.
    usage = Usage(input_tokens=1_000_000, output_tokens=0, cached_tokens=600_000)
    assert estimate_cost("muse-spark-1.3", usage) == pytest.approx(0.59, abs=1e-12)


def test_wonder_price_and_longest_prefix():
    assert price_for("muse-wonder-1.0") is PRICES["muse-wonder"]
    assert price_for("muse-spark-1.2") is PRICES["muse-spark"]
    assert estimate_cost("muse-wonder-1.0", Usage(1_000_000, 1_000_000)) == pytest.approx(24.0)


def test_unknown_model_raises_instead_of_costing_zero():
    with pytest.raises(ValueError, match="no price"):
        price_for("gpt-4o")


def test_usage_from_chat_completions_shape():
    u = Usage.from_response({
        "prompt_tokens": 1200, "completion_tokens": 5000,
        "prompt_tokens_details": {"cached_tokens": 1000},
        "completion_tokens_details": {"reasoning_tokens": 3000},
    })
    assert u == Usage(input_tokens=1200, output_tokens=5000, cached_tokens=1000, reasoning_tokens=3000)


def test_usage_from_responses_api_shape_and_missing_details():
    u = Usage.from_response({"input_tokens": 10, "output_tokens": 20,
                             "input_tokens_details": None})
    assert u == Usage(input_tokens=10, output_tokens=20)
    assert Usage.from_response({}) == Usage(0, 0)
