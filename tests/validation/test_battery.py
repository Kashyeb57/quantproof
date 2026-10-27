"""Tests for quantproof/validation/battery.py (the proof battery).

Offline and deterministic: all Monte Carlo work uses fixed seeds and
n_paths=20_000, as specified in the task, to keep this file under ~60 s.
No network, filing downloads, or AI calls are used.

Tolerance justification: this file does not invent pricing tolerances. It
asserts the CheckResult.passed flags and tolerances produced by battery.py,
whose tolerances are documented there (principally 3 Monte Carlo standard
errors, 2 standard errors for common-random-number monotonicity steps, and
the stated deterministic/algebraic tolerances). Where this file compares
numbers directly, the comment on that assertion gives the reason.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import date, timedelta

import pytest

import quantproof.validation.battery as battery
from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import (
    BarrierObservation,
    Cited,
    ContingentCoupon,
    CouponObservation,
    Dependency,
    Downside,
    DownsideKind,
    TermSheet,
    Underlying,
    Upside,
)
from quantproof.validation.battery import (
    check_barrier_at_maturity,
    check_barrier_removed,
    check_bsm_known_values,
    check_determinism,
    check_put_call_parity,
    check_quotes,
    run_battery,
)
from quantproof.validation.quotes import verify_termsheet

N_PATHS = 20_000
SEED = 1

# Cache only the expensive JPM battery run so test 1 and the numeric-fields
# test share one identical, deterministic run when the whole file is run.
# If the numeric-fields test is run alone, the cache is empty and it runs
# the battery itself. The mutation test deliberately does not use this
# cache: it must observe run_battery acting on its own term-sheet object.
_JPM_BATTERY_CACHE: dict[str, list] = {}


def _jpm_market(ts: TermSheet) -> MarketInputs:
    """Market inputs specified in the task for the JPM fixture note."""
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17),
        dividend_yields=(0.012, 0.013),
        correlation=((1.0, 0.6), (0.6, 1.0)),
        credit_spread=0.006,
    )


def _jpm_results(ts: TermSheet, market: MarketInputs) -> list:
    if "results" not in _JPM_BATTERY_CACHE:
        _JPM_BATTERY_CACHE["results"] = run_battery(
            ts, market, n_paths=N_PATHS, seed=SEED
        )
    return _JPM_BATTERY_CACHE["results"]


def _failed_summary(results: list) -> str:
    """Human-readable list of failed checks, for assertion messages."""
    return "; ".join(
        f"{r.name}: {r.detail} "
        f"(value={r.value!r}, reference={r.reference!r}, tolerance={r.tolerance!r})"
        for r in results
        if not r.passed
    )


def _single_asset_note(
    *,
    downside_kind: DownsideKind,
    downside_level: float,
    downside_gearing: float,
    downside_observation: BarrierObservation,
    upside: Upside | None = None,
    coupon: ContingentCoupon | None = None,
) -> TermSheet:
    """Build the inline one-asset note described in the task.

    Dates: pricing 2025-01-02, issue 2025-01-07, final review 2026-01-02,
    maturity 2026-01-07. Notional and issue price are both 1,000. Values are
    set by us, so Cited carries no citation (citation=None), exactly as the
    term-sheet schema allows for self-set values.
    """
    return TermSheet(
        issuer=Cited("Test Issuer"),
        is_preliminary=Cited(False),
        underlyings=[
            Underlying(name=Cited("Test Index"), ticker=Cited("TEST"))
        ],
        dependency=Cited(Dependency.WORST_OF),
        notional=Cited(1000.0),
        issue_price=Cited(1000.0),
        pricing_date=Cited(date(2025, 1, 2)),
        issue_date=Cited(date(2025, 1, 7)),
        final_review_date=Cited(date(2026, 1, 2)),
        maturity_date=Cited(date(2026, 1, 7)),
        downside=Downside(
            kind=Cited(downside_kind),
            level=Cited(downside_level),
            observation=Cited(downside_observation),
            gearing=Cited(downside_gearing),
        ),
        autocall=None,
        coupon=coupon,
        upside=upside,
    )


# 1. Full battery on the JPM worst-of autocallable fixture -------------------


def test_run_battery_jpm_fixture_all_pass(jpm_38582_termsheet) -> None:
    ts = jpm_38582_termsheet
    market = _jpm_market(ts)

    results = _jpm_results(ts, market)
    names = {r.name for r in results}

    assert results, "run_battery returned no results"
    # No filing_text was passed, so the quotes check must not run.
    assert "quotes" not in names
    # Applicability checks required by the task for this two-asset,
    # autocallable, coupon-bearing note.
    assert "correlation_one" in names
    assert "correlation_monotonicity" in names
    assert "never_callable" in names
    assert "convergence" in names

    failed = _failed_summary(results)
    assert all(r.passed for r in results), f"failed battery checks: {failed}"


# 2. One-asset buffered note: correlation / feature checks must not apply ----


def test_run_battery_single_asset_buffered_note() -> None:
    pricing = date(2025, 1, 2)
    ts = _single_asset_note(
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.9,
        # Buffer gearing 1 / 0.9, exactly as specified in the task.
        downside_gearing=1.0 / 0.9,
        downside_observation=BarrierObservation.FINAL_ONLY,
        upside=Upside(leverage=Cited(1.5), cap=Cited(0.2)),
        coupon=None,
    )
    market = MarketInputs.single(
        as_of=pricing,
        risk_free_rate=0.04,
        vol=0.2,
        dividend_yield=0.01,
        credit_spread=0.005,
    )

    results = run_battery(ts, market, n_paths=N_PATHS, seed=SEED)
    names = {r.name for r in results}

    # One underlying: no correlation checks are applicable.
    assert "correlation_one" not in names
    assert "correlation_monotonicity" not in names
    # An upside leg means the note is not a short-put note, so the
    # vol-monotonicity applicability rule in run_battery excludes it.
    assert "vol_monotonicity" not in names
    # No autocall and no coupon: never_callable is not applicable.
    assert "never_callable" not in names
    # No coupon: coupon_monotonicity is not applicable.
    assert "coupon_monotonicity" not in names

    failed = _failed_summary(results)
    assert all(r.passed for r in results), f"failed battery checks: {failed}"


# 3. One-asset memory contingent-coupon note ---------------------------------


def test_run_battery_memory_coupon_note() -> None:
    pricing = date(2025, 1, 2)
    review_dates = [
        date(2025, 4, 2),
        date(2025, 7, 2),
        date(2025, 10, 2),
        date(2026, 1, 2),
    ]
    coupon = ContingentCoupon(
        barrier=Cited(0.7),
        amount=Cited(20.0),
        memory=Cited(True),
        observations=[
            CouponObservation(
                review_date=Cited(review),
                # Payment five days after review; all payments are on or
                # after their review dates and strictly increasing.
                payment_date=Cited(review + timedelta(days=5)),
            )
            for review in review_dates
        ],
    )
    ts = _single_asset_note(
        downside_kind=DownsideKind.BARRIER,
        downside_level=0.6,
        downside_gearing=1.0,
        downside_observation=BarrierObservation.EACH_REVIEW,
        upside=None,
        coupon=coupon,
    )
    market = MarketInputs.single(
        as_of=pricing,
        risk_free_rate=0.04,
        vol=0.2,
        dividend_yield=0.01,
        credit_spread=0.005,
    )

    results = run_battery(ts, market, n_paths=N_PATHS, seed=SEED)
    by_name = {r.name: r for r in results}

    # A coupon makes both of these checks applicable (never_callable via
    # "autocall or coupon", coupon_monotonicity via "coupon present").
    assert "never_callable" in by_name
    assert "coupon_monotonicity" in by_name
    assert by_name["never_callable"].passed, (
        f"never_callable failed: {by_name['never_callable'].detail} "
        f"(value={by_name['never_callable'].value!r}, "
        f"reference={by_name['never_callable'].reference!r}, "
        f"tolerance={by_name['never_callable'].tolerance!r})"
    )
    assert by_name["coupon_monotonicity"].passed, (
        f"coupon_monotonicity failed: {by_name['coupon_monotonicity'].detail} "
        f"(value={by_name['coupon_monotonicity'].value!r}, "
        f"reference={by_name['coupon_monotonicity'].reference!r}, "
        f"tolerance={by_name['coupon_monotonicity'].tolerance!r})"
    )


# 4. Failure detection: a +50 price error must be caught ---------------------


def test_battery_detects_inflated_price(jpm_38582_termsheet, monkeypatch) -> None:
    ts = jpm_38582_termsheet
    market = _jpm_market(ts)
    real_price_note = battery.price_note

    def inflated_price_note(ts_arg, market_arg, n_paths=N_PATHS, seed=SEED):
        real_result = real_price_note(
            ts_arg, market_arg, n_paths=n_paths, seed=seed
        )
        # +50 on a 1,000-notional note is far larger than the battery's
        # 3-standard-error tolerance at 20,000 paths (standard errors here
        # are of order a few currency units), so a correct check must fail.
        return dataclasses.replace(
            real_result, price=real_result.price + 50.0
        )

    monkeypatch.setattr(battery, "price_note", inflated_price_note)

    barrier_result = check_barrier_at_maturity(
        ts, market, n_paths=N_PATHS, seed=SEED
    )
    removed_result = check_barrier_removed(
        ts, market, n_paths=N_PATHS, seed=SEED
    )

    assert barrier_result.passed is False, (
        "check_barrier_at_maturity should fail when every Monte Carlo "
        f"price is inflated by 50.0: {barrier_result.detail}"
    )
    assert removed_result.passed is False, (
        "check_barrier_removed should fail when every Monte Carlo price "
        f"is inflated by 50.0: {removed_result.detail}"
    )


# 5. Black-Scholes textbook values and put-call parity -----------------------


def test_bsm_known_values_and_put_call_parity() -> None:
    bsm = check_bsm_known_values()
    assert bsm.passed, f"bsm_known_values failed: {bsm.detail}"
    # battery.py compares against Hull's published two-decimal values with
    # tolerance 0.005 (half a cent, i.e. the rounding of 4.76 and 0.81).
    assert bsm.value == pytest.approx(4.76, abs=0.005)
    assert abs(bsm.data["put"] - 0.81) <= 0.005

    parity = check_put_call_parity()
    assert parity.passed, f"put_call_parity failed: {parity.detail}"
    # Parity is an algebraic identity of the two closed forms, so battery.py
    # allows only 1e-10 (floating-point rounding); assert that identity here.
    assert parity.value is not None and parity.reference is not None
    assert abs(parity.value - parity.reference) <= 1e-10


# 6. run_battery must not mutate the caller's term sheet ----------------------


def test_run_battery_does_not_mutate_termsheet(jpm_38582_termsheet) -> None:
    ts = jpm_38582_termsheet
    market = _jpm_market(ts)
    before = copy.deepcopy(ts)

    run_battery(ts, market, n_paths=N_PATHS, seed=SEED)

    # TermSheet is a dataclass tree, so == is field-by-field dataclass
    # equality over the whole copied tree, including nested Cited values.
    assert ts == before


# 7. Determinism: identical seed, identical price, zero tolerance ------------


def test_check_determinism_exact(jpm_38582_termsheet) -> None:
    market = _jpm_market(jpm_38582_termsheet)

    result = check_determinism(
        jpm_38582_termsheet, market, n_paths=N_PATHS, seed=SEED
    )

    assert result.passed, f"determinism failed: {result.detail}"
    # Identical seeds must give bit-identical prices: tolerance exactly 0.
    assert result.tolerance == 0
    assert result.value == result.reference


# 8. Quotes: an unverifiable citation is flagged, never silently used --------


def test_check_quotes_flags_replaced_quote(jpm_38582_termsheet) -> None:
    ts = copy.deepcopy(jpm_38582_termsheet)

    # Take the first cited field that actually carries a citation and break
    # its quote. We discover the path rather than hard-coding it, so the
    # test still refers to the field it actually changed.
    changed_path = None
    for path, cited in ts.cited_fields():
        if cited.citation is not None:
            cited.citation.quote = "NOT IN FILING"
            changed_path = path
            break
    assert changed_path is not None, "fixture has no cited field with a citation"

    # Cross-check the verifier API named in the task: it returns objects
    # with .path and .verified, and the changed field must be unverified
    # against empty filing text (no quote can appear in an empty text).
    quote_checks = verify_termsheet(ts, "")
    by_path = {c.path: c for c in quote_checks}
    assert changed_path in by_path
    assert by_path[changed_path].verified is False

    result = check_quotes(ts, "")
    assert result.passed is False
    assert changed_path in result.data["unverified"]

    # The same must hold for a tiny filing text that cannot contain the
    # replaced quote (or, indeed, any genuine quote from the fixture).
    tiny_result = check_quotes(ts, "tiny filing text")
    assert tiny_result.passed is False
    assert changed_path in tiny_result.data["unverified"]


# 9. Numeric fields: every non-BSM result reports a tolerance -----------------


def test_all_non_bsm_results_have_tolerance(jpm_38582_termsheet) -> None:
    ts = jpm_38582_termsheet
    market = _jpm_market(ts)
    results = _jpm_results(ts, market)

    bsm_names = {"bsm_known_values", "put_call_parity"}
    non_bsm = [r for r in results if r.name not in bsm_names]
    assert non_bsm, "expected non-BSM battery results to inspect"
    for result in non_bsm:
        # Every Monte Carlo / monotonicity / determinism check must state
        # the numeric tolerance it used (0.0 is a valid stated tolerance;
        # None would mean the tolerance was never reported).
        assert result.tolerance is not None, (
            f"{result.name} reported tolerance=None: {result.detail}"
        )
