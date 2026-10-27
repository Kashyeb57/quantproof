"""Offline tests for the synthesized-payoff proof battery.

Uses the saved ea0306095 filing and recorded reply fixtures (a
dual-directional buffered note).  No network and no API call: the generated
payoff under test is a hand-written reference string, and every sandbox run
is a local subprocess on fixed-seed simulated paths.

Note terms (from the fixture): R = min_i perf_final - 1, buffer 0.40
(downside level 0.6), upside leverage 2; at maturity R > 0 pays
1000 (1 + 2 R), -0.40 <= R <= 0 pays 1000 (1 + |R|), R < -0.40 pays
1000 (1 + R + 0.40); one autocall review ($1,350 per note per the term
sheet).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantproof.agent.extract import parse_json_reply, to_termsheet
from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import Citation, Cited
from quantproof.ingest.html import read_filing
from quantproof.models.discounting import discount_factor, year_fraction
from quantproof.models.montecarlo import mc_mean_and_se, simulate_performance
from quantproof.payoffs.synthesized import (
    cash_from_code,
    payment_dates,
    price_cashflows,
    terms_from_termsheet,
)
from quantproof.payoffs.worst_of import observation_dates
from quantproof.validation.synth_battery import (
    PayoutExample,
    battery_passed,
    check_called_paths_match_template,
    check_filing_examples,
    check_output_sanity,
    run_synth_battery,
)

STEM = "jpm_424b2-ea0306095"
FILING = ROOT / "tests" / "fixtures" / "filings" / f"{STEM}.htm"
REPLY = ROOT / "tests" / "fixtures" / "extractions" / f"{STEM}.reply.json"

N_PATHS = 8_000
SEED = 0

REFERENCE_CODE = """
import numpy as np

def cashflows(perf, terms):
    n_paths = perf.shape[0]
    n_dates = perf.shape[1]
    cash = np.zeros((n_paths, n_dates))
    worst = np.min(perf, axis=2)
    notional = terms["notional"]
    final_index = terms["final_index"]
    autocall = terms["autocall"]
    trigger = autocall["trigger"] if autocall is not None else 0.0
    call_amounts = autocall["call_amounts"] if autocall is not None else [None] * n_dates
    downside = terms["downside"]
    level = downside["level"]
    upside = terms["upside"]
    leverage = upside["leverage"] if upside is not None else 1.0
    alive = np.ones(n_paths, dtype=bool)
    for k in range(n_dates):
        if autocall is not None:
            call_amount = call_amounts[k]
            if call_amount is not None:
                called = alive & (worst[:, k] >= trigger)
                cash[called, k] += call_amount
                alive[called] = False
        if k == final_index:
            w = worst[:, k]
            r = w - 1.0
            pay = np.where(r > 0.0, notional * (1.0 + leverage * r), np.where(w >= level, notional * (1.0 + np.absolute(r)), notional * (1.0 + r + (1.0 - level))))
            cash[alive, k] += pay[alive]
    return cash
"""


def _example(worst_final: float, payment: float, quote: str) -> PayoutExample:
    return PayoutExample(
        worst_final=worst_final,
        payment=Cited(payment, Citation(quote=quote)),
    )


# One row per line of the filing's hypothetical payment table; quotes verbatim.
EXAMPLES = [
    _example(1.65, 2300.00, "$165.00 65.00% N/A 130.00% $2,300.00"),
    _example(0.95, 1050.00, "$95.00 -5.00% 5.00% 5.00% $1,050.00"),
    _example(0.60, 1400.00, "$60.00 -40.00% 40.00% 40.00% $1,400.00"),
    _example(0.50, 900.00, "$50.00 -50.00% N/A -10.00% $900.00"),
    _example(1.00, 1000.00, "$100.00 0.00% 0.00% 0.00% $1,000.00"),
    _example(1.20, 1400.00, "$120.00 20.00% N/A 40.00% $1,400.00"),
]


@pytest.fixture(scope="module")
def ts():
    return to_termsheet(parse_json_reply(REPLY.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def filing_text():
    return read_filing(FILING.read_bytes()).text


@pytest.fixture(scope="module")
def market(ts):
    n = ts.n_underlyings
    correlation = tuple(
        tuple(1.0 if i == j else 0.5 for j in range(n)) for i in range(n)
    )
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.30, 0.35, 0.28),
        dividend_yields=(0.0, 0.01, 0.012),
        correlation=correlation,
        credit_spread=0.006,
        sources={"all": "test-entered"},
    )


@pytest.fixture(scope="module")
def battery_results(ts, market, filing_text):
    return run_synth_battery(
        REFERENCE_CODE,
        ts,
        market,
        EXAMPLES,
        n_paths=N_PATHS,
        seed=SEED,
        filing_text=filing_text,
    )


def _by_name(results):
    return {result.name: result for result in results}


def test_reference_code_passes_every_check(battery_results):
    by_name = _by_name(battery_results)
    for name, result in by_name.items():
        assert result.passed, f"{name} failed: {result.detail} data={result.data}"
    assert battery_passed(battery_results)


def test_reference_price_is_finite_with_se_and_provenance(ts, market):
    result = price_cashflows(
        ts, market, cash_from_code(REFERENCE_CODE), n_paths=N_PATHS, seed=SEED
    )
    assert np.isfinite(result.price)
    assert result.std_error is not None and result.std_error > 0
    assert result.seed == SEED
    assert result.paths == N_PATHS


def test_mutant_without_absolute_value_fails_filing_examples(ts, filing_text):
    mutant = REFERENCE_CODE.replace("np.absolute(r)", "0.0")
    assert mutant != REFERENCE_CODE
    result = check_filing_examples(mutant, ts, EXAMPLES, filing_text=filing_text)
    assert not result.passed


def test_mutant_leverage_one_fails_filing_examples(ts, filing_text):
    mutant = REFERENCE_CODE.replace(
        'leverage = upside["leverage"] if upside is not None else 1.0',
        "leverage = 1.0",
    )
    assert mutant != REFERENCE_CODE
    result = check_filing_examples(mutant, ts, EXAMPLES, filing_text=filing_text)
    assert not result.passed


def test_mutant_ignoring_autocall_fails_called_paths(ts, market):
    mutant = REFERENCE_CODE.replace(
        "if call_amount is not None:", "if call_amount is None:"
    )
    assert mutant != REFERENCE_CODE
    result = check_called_paths_match_template(
        mutant, ts, market, n_paths=N_PATHS, seed=SEED
    )
    assert not result.passed


def test_mutant_paying_call_and_maturity_fails(ts, market):
    mutant = REFERENCE_CODE.replace("alive[called] = False", "alive[called] = True")
    assert mutant != REFERENCE_CODE
    sanity = check_output_sanity(mutant, ts, market, n_paths=N_PATHS, seed=SEED)
    called = check_called_paths_match_template(
        mutant, ts, market, n_paths=N_PATHS, seed=SEED
    )
    assert (not sanity.passed) or (not called.passed)


def test_mutant_negative_cash_fails_output_sanity(ts, market):
    mutant = REFERENCE_CODE.replace("return cash", "return cash - 1.0")
    assert mutant != REFERENCE_CODE
    result = check_output_sanity(mutant, ts, market, n_paths=N_PATHS, seed=SEED)
    assert not result.passed


def test_mutant_using_random_fails_battery(ts, market, filing_text):
    mutant = REFERENCE_CODE.replace(
        "    cash = np.zeros((n_paths, n_dates))",
        "    np.random.standard_normal(1)\n    cash = np.zeros((n_paths, n_dates))",
    )
    assert mutant != REFERENCE_CODE
    results = run_synth_battery(
        mutant, ts, market, EXAMPLES, n_paths=N_PATHS, seed=SEED,
        filing_text=filing_text,
    )
    assert not battery_passed(results)


def test_mutant_import_os_gives_only_static_failure(ts, market, filing_text):
    mutant = "import os\n" + REFERENCE_CODE
    results = run_synth_battery(
        mutant, ts, market, EXAMPLES, n_paths=N_PATHS, seed=SEED,
        filing_text=filing_text,
    )
    assert len(results) == 1
    assert results[0].name == "static_safety"
    assert not results[0].passed
    assert not battery_passed(results)


def test_only_two_verified_examples_fails_filing_examples(ts, filing_text):
    examples = [
        EXAMPLES[0],
        EXAMPLES[1],
        *[
            PayoutExample(
                worst_final=ex.worst_final,
                payment=Cited(
                    float(ex.payment.value),
                    Citation(quote="this quote is fabricated and not in the filing"),
                ),
            )
            for ex in EXAMPLES[2:]
        ],
    ]
    result = check_filing_examples(
        REFERENCE_CODE, ts, examples, filing_text=filing_text
    )
    assert not result.passed
    assert "not enough filing evidence" in result.detail


def test_terms_from_termsheet_is_json_serialisable_and_aligned(ts):
    terms = terms_from_termsheet(ts)
    json.dumps(terms)
    dates = observation_dates(ts)
    assert len(terms["observation_dates"]) == len(dates)
    assert len(terms["payment_dates"]) == len(dates)
    assert terms["final_index"] == dates.index(ts.final_review_date.value)
    if terms["autocall"] is not None:
        assert len(terms["autocall"]["call_amounts"]) == len(dates)
    if terms["coupon"] is not None:
        assert len(terms["coupon"]["amounts"]) == len(dates)
    pays = payment_dates(ts, dates)
    assert pays[terms["final_index"]] == ts.maturity_date.value
    assert terms["payment_dates"][terms["final_index"]] == (
        ts.maturity_date.value.isoformat()
    )


def test_price_cashflows_matches_direct_numpy_computation(ts, market):
    pricing = ts.pricing_date.value
    dates = observation_dates(ts)
    times = [year_fraction(pricing, d) for d in dates]
    perf = simulate_performance(
        times,
        market.vols,
        market.dividend_yields,
        np.asarray(market.correlation, dtype=float),
        drifts=(float(market.risk_free_rate),) * ts.n_underlyings,
        n_paths=N_PATHS,
        seed=SEED,
        antithetic=True,
    )
    terms = terms_from_termsheet(ts)
    worst = np.min(perf, axis=2)
    cash = np.zeros((N_PATHS, len(dates)))
    alive = np.ones(N_PATHS, dtype=bool)
    notional = float(terms["notional"])
    level = float(terms["downside"]["level"])
    leverage = float(terms["upside"]["leverage"])
    trigger = float(terms["autocall"]["trigger"])
    call_amounts = terms["autocall"]["call_amounts"]
    for k in range(len(dates)):
        call_amount = call_amounts[k]
        if call_amount is not None:
            called = alive & (worst[:, k] >= trigger)
            cash[called, k] += float(call_amount)
            alive[called] = False
        if k == terms["final_index"]:
            w = worst[:, k]
            r = w - 1.0
            pay = np.where(
                r > 0.0,
                notional * (1.0 + leverage * r),
                np.where(
                    w >= level,
                    notional * (1.0 + np.absolute(r)),
                    notional * (1.0 + r + (1.0 - level)),
                ),
            )
            cash[alive, k] += pay[alive]
    pays = payment_dates(ts, dates)
    dfs = np.array(
        [
            float(
                discount_factor(
                    year_fraction(pricing, pay),
                    float(market.risk_free_rate),
                    float(market.credit_spread),
                )
            )
            for pay in pays
        ]
    )
    pv = cash @ dfs
    mean, _se = mc_mean_and_se(pv, True)

    result = price_cashflows(
        ts, market, cash_from_code(REFERENCE_CODE), n_paths=N_PATHS, seed=SEED
    )
    # 1e-9 absolute: the wrapper must add no error beyond identical float64 arithmetic
    assert abs(result.price - mean) <= 1e-9
