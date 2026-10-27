"""Tests for the worst-of payoff template. All hand cases state the expected number."""

from datetime import date, timedelta

import numpy as np
import pytest
from quantproof.models import bsm

from quantproof.core import (
    Autocall,
    AutocallObservation,
    BarrierObservation,
    Cited,
    ContingentCoupon,
    CouponObservation,
    Dependency,
    Downside,
    DownsideKind,
    MarketInputs,
    Measure,
    TermSheet,
    Underlying,
    Upside,
)
from quantproof.payoffs.worst_of import (
    UnsupportedTermSheet,
    check_supported,
    observation_dates,
    pathwise_cashflows,
    price_note,
)


def make_ts(
    n_assets=1,
    notional=1000.0,
    pricing=date(2025, 1, 1),
    issue=date(2025, 1, 5),
    final=date(2026, 1, 1),
    maturity=date(2026, 1, 10),
    downside_kind=DownsideKind.BARRIER,
    downside_level=0.7,
    downside_obs=BarrierObservation.FINAL_ONLY,
    gearing=1.0,
    autocall_trigger=1.0,
    autocall_data=None,
    coupon_barrier=0.6,
    coupon_amount=10.0,
    coupon_memory=True,
    coupon_data=None,
    upside_data=None,
    unsupported=None,
):
    underlyings = [Underlying(name=Cited(f"Asset {i}")) for i in range(n_assets)]
    downside = Downside(
        kind=Cited(downside_kind),
        level=Cited(downside_level),
        observation=Cited(downside_obs),
        gearing=Cited(gearing),
    )
    autocall = None
    if autocall_data is not None:
        obs = [
            AutocallObservation(
                review_date=Cited(r),
                payment_date=Cited(p),
                call_amount=Cited(a),
            )
            for (r, p, a) in autocall_data
        ]
        autocall = Autocall(trigger=Cited(autocall_trigger), observations=obs)
    coupon = None
    if coupon_data is not None:
        obs = [CouponObservation(review_date=Cited(r), payment_date=Cited(p)) for (r, p) in coupon_data]
        coupon = ContingentCoupon(
            barrier=Cited(coupon_barrier),
            amount=Cited(coupon_amount),
            memory=Cited(coupon_memory),
            observations=obs,
        )
    upside = None
    if upside_data is not None:
        lev, cap = upside_data
        upside = Upside(leverage=Cited(lev), cap=Cited(cap) if cap is not None else None)
    unsup = [Cited(s) for s in (unsupported or [])]
    return TermSheet(
        issuer=Cited("Test Issuer"),
        is_preliminary=Cited(False),
        underlyings=underlyings,
        dependency=Cited(Dependency.WORST_OF),
        notional=Cited(notional),
        issue_price=Cited(notional),
        pricing_date=Cited(pricing),
        issue_date=Cited(issue),
        maturity_date=Cited(maturity),
        final_review_date=Cited(final),
        downside=downside,
        autocall=autocall,
        coupon=coupon,
        upside=upside,
        unsupported_features=unsup,
    )


def one():
    return lambda d: 1.0


def test_worst_of_picks_min():
    # Two assets with perf (1.2, 0.5) at maturity, barrier 0.7 final-only -> 500 per 1000.
    ts = make_ts(n_assets=2, downside_level=0.7)
    dates = observation_dates(ts)
    perf = np.array([[[1.2, 0.5]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(500.0)


def test_barrier_final_only():
    # W_T = 0.75 -> 1000; W_T = 0.6 -> 600; W_T = 0.0 -> 0 (N=1000, L=0.7, g=1).
    ts = make_ts(n_assets=1, downside_level=0.7, gearing=1.0)
    dates = observation_dates(ts)
    perf = np.array([[[0.75]], [[0.6]], [[0.0]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1000.0)
    assert pv[1] == pytest.approx(600.0)
    assert pv[2] == pytest.approx(0.0)


def test_buffer():
    # Level 0.825, gearing 1.21212, W_T=0.5 -> 1000*(1-1.21212*0.325) = 606.061; W_T=0 -> ~0.
    ts = make_ts(
        n_assets=1,
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.825,
        gearing=1.21212,
    )
    dates = observation_dates(ts)
    perf = np.array([[[0.5]], [[0.0]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    expected = 1000.0 * (1.0 - 1.21212 * 0.325)  # 606.061
    assert pv[0] == pytest.approx(expected)
    assert pv[1] == pytest.approx(0.0, abs=0.01)


def test_upside_capped():
    # Leverage 2, cap 0.15, W_T=1.1 -> 1150.
    ts = make_ts(n_assets=1, downside_level=0.7, upside_data=(2.0, 0.15))
    dates = observation_dates(ts)
    pv = pathwise_cashflows(ts, dates, np.array([[[1.1]]]), one())
    assert pv[0] == pytest.approx(1150.0)


def test_upside_uncapped():
    # Leverage 2, cap None, W_T=1.1 -> 1200.
    ts = make_ts(n_assets=1, downside_level=0.7, upside_data=(2.0, None))
    dates = observation_dates(ts)
    pv = pathwise_cashflows(ts, dates, np.array([[[1.1]]]), one())
    assert pv[0] == pytest.approx(1200.0)


def test_upside_below_one_with_buffer():
    # W_T=0.9 with buffer 0.8 -> 1000 (no upside below 1, above buffer).
    ts = make_ts(
        n_assets=1,
        downside_kind=DownsideKind.BUFFER,
        downside_level=0.8,
        upside_data=(2.0, 0.15),
    )
    dates = observation_dates(ts)
    pv = pathwise_cashflows(ts, dates, np.array([[[0.9]]]), one())
    assert pv[0] == pytest.approx(1000.0)


def _autocall_ts():
    r1 = date(2025, 4, 1)
    r2 = date(2025, 7, 1)
    r3 = date(2025, 10, 1)
    final = date(2026, 1, 1)
    mat = date(2026, 1, 10)
    def pay(r):
        return r + timedelta(days=5)
    data = [(r1, pay(r1), 1050.0), (r2, pay(r2), 1100.0), (r3, pay(r3), 1150.0)]
    ts = make_ts(
        n_assets=1,
        final=final,
        maturity=mat,
        downside_level=0.7,
        autocall_trigger=1.0,
        autocall_data=data,
    )
    return ts, [r1, r2, r3, final], data


def test_autocall_called_on_date2():
    # 3 reviews, final not an autocall date; W=[0.9,1.05,...] -> only call_amount_2 = 1100.
    ts, _, data = _autocall_ts()
    dates = observation_dates(ts)
    assert dates == sorted(dates)
    assert dates[-1] == date(2026, 1, 1)
    perf = np.array([[[0.9], [1.05], [0.5], [0.5]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1100.0)  # 1100


def test_autocall_never_called_above_barrier():
    # Never called and W_T=0.8 above barrier 0.7 -> 1000.
    ts, _, _ = _autocall_ts()
    dates = observation_dates(ts)
    perf = np.array([[[0.9], [0.9], [0.9], [0.8]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1000.0)


def test_autocall_discounted():
    # Same call with discount 0.9 on the date-2 payment date -> 0.9*call_amount_2 = 990.
    ts, _, data = _autocall_ts()
    dates = observation_dates(ts)
    pay2 = data[1][1]
    def disc(d):
        return 0.9 if d == pay2 else 1.0
    perf = np.array([[[0.9], [1.05], [0.5], [0.5]]])
    pv = pathwise_cashflows(ts, dates, perf, disc)
    assert pv[0] == pytest.approx(0.9 * 1100.0)  # 990


def _coupon_ts(memory):
    r1 = date(2025, 4, 1)
    r2 = date(2025, 7, 1)
    r3 = date(2026, 1, 1)  # equals final
    final = date(2026, 1, 1)
    mat = date(2026, 1, 10)
    def pay(r):
        return r + timedelta(days=5)
    data = [(r1, pay(r1)), (r2, pay(r2)), (r3, pay(r3))]
    ts = make_ts(
        n_assets=1,
        final=final,
        maturity=mat,
        downside_level=0.5,
        coupon_barrier=0.6,
        coupon_amount=10.0,
        coupon_memory=memory,
        coupon_data=data,
    )
    return ts


def test_coupon_memory():
    # W path 0.5,0.5,0.9 barrier 0.6 amount 10.0 memory -> coupons 3*10=30 + 1000 = 1030.
    ts = _coupon_ts(memory=True)
    dates = observation_dates(ts)
    perf = np.array([[[0.5], [0.5], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1030.0)  # 1000 principal + 30 memory coupon


def test_coupon_no_memory():
    # Same path without memory -> 10 + 1000 = 1010.
    ts = _coupon_ts(memory=False)
    dates = observation_dates(ts)
    perf = np.array([[[0.5], [0.5], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1010.0)


def test_coupon_only_where_above_barrier():
    # W path 0.9,0.5,0.9 with memory False -> coupons on dates 1 and 3 = 20 + 1000 = 1020.
    ts = _coupon_ts(memory=False)
    dates = observation_dates(ts)
    perf = np.array([[[0.9], [0.5], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(1020.0)


def test_call_stops_later_coupons():
    # Coupon + autocall on same dates; call on date 1 pays coupon1 + principal call, stops rest.
    r1 = date(2025, 4, 1)
    r2 = date(2025, 7, 1)
    final = date(2026, 1, 1)
    mat = date(2026, 1, 10)
    def pay(r):
        return r + timedelta(days=5)
    ts = make_ts(
        n_assets=1,
        final=final,
        maturity=mat,
        downside_level=0.7,
        autocall_trigger=1.0,
        autocall_data=[(r1, pay(r1), 1000.0), (r2, pay(r2), 1000.0)],
        coupon_barrier=0.6,
        coupon_amount=10.0,
        coupon_memory=False,
        coupon_data=[(r1, pay(r1)), (r2, pay(r2)), (final, final + timedelta(days=5))],
    )
    dates = observation_dates(ts)
    perf = np.array([[[1.05], [0.9], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    # Coupon date 1 (10) + call principal (1000); later coupons and maturity never paid.
    assert pv[0] == pytest.approx(1010.0)


def test_monthly_coupons_autocall_from_date3():
    # Monthly coupons r1..r5, autocall from date 3; call on date 4 stops date-5 coupon.
    reviews = [date(2025, 3, 1), date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1), date(2026, 1, 1)]
    final = date(2026, 1, 1)
    mat = date(2026, 1, 10)
    def pay(r):
        return r + timedelta(days=5)
    ts = make_ts(
        n_assets=1,
        final=final,
        maturity=mat,
        downside_level=0.7,
        autocall_trigger=1.0,
        autocall_data=[(reviews[2], pay(reviews[2]), 1000.0), (reviews[3], pay(reviews[3]), 1000.0)],
        coupon_barrier=0.6,
        coupon_amount=10.0,
        coupon_memory=False,
        coupon_data=[(r, pay(r)) for r in reviews],
    )
    dates = observation_dates(ts)
    assert len(dates) == 5  # union of monthly coupons and later autocalls plus final
    perf = np.array([[[0.9], [0.9], [0.9], [1.05], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    # Coupons on dates 1-4 (40) + call principal 1000 on date 4; date 5 never paid.
    assert pv[0] == pytest.approx(1040.0)


def test_each_review_barrier():
    # Breached early (0.6), W_T=0.9 -> 900; never breached (0.8), W_T=0.9 -> 1000.
    early = date(2025, 7, 1)
    final = date(2026, 1, 1)
    mat = date(2026, 1, 10)
    def pay(r):
        return r + timedelta(days=5)
    ts = make_ts(
        n_assets=1,
        final=final,
        maturity=mat,
        downside_kind=DownsideKind.BARRIER,
        downside_level=0.7,
        downside_obs=BarrierObservation.EACH_REVIEW,
        gearing=1.0,
        autocall_trigger=5.0,  # never called; provides the early observation date
        autocall_data=[(early, pay(early), 1000.0)],
    )
    dates = observation_dates(ts)
    assert dates == [early, final]
    perf = np.array([[[0.6], [0.9]], [[0.8], [0.9]]])
    pv = pathwise_cashflows(ts, dates, perf, one())
    assert pv[0] == pytest.approx(900.0)  # breached and W_T<1 -> 1000*(1+(0.9-1))
    assert pv[1] == pytest.approx(1000.0)  # never breached -> full principal


def test_refusal_unsupported_features():
    ts = make_ts(unsupported=["CMS leg"])
    with pytest.raises(UnsupportedTermSheet, match="CMS leg"):
        check_supported(ts)


def test_refusal_daily_observation():
    ts = make_ts(downside_obs=BarrierObservation.DAILY)
    with pytest.raises(UnsupportedTermSheet):
        check_supported(ts)


def test_refusal_as_of_mismatch():
    ts = make_ts()
    market = MarketInputs.single(
        as_of=date(2025, 1, 2), risk_free_rate=0.03, vol=0.2, dividend_yield=0.0
    )
    with pytest.raises(ValueError, match="as of the pricing date"):
        price_note(ts, market, n_paths=10, seed=0)


def test_refusal_wrong_vols_length():
    ts = make_ts(n_assets=2)
    market = MarketInputs.single(
        as_of=date(2025, 1, 1), risk_free_rate=0.03, vol=0.2, dividend_yield=0.0
    )
    with pytest.raises(ValueError):
        price_note(ts, market, n_paths=10, seed=0)


def test_observation_dates_rejects_after_final():
    ts = make_ts(final=date(2026, 1, 1), autocall_data=[(date(2026, 2, 1), date(2026, 2, 6), 1050.0)])
    with pytest.raises(ValueError):
        observation_dates(ts)


def test_price_note_conftest(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = MarketInputs(
        as_of=date(2025, 11, 25),
        risk_free_rate=0.03,
        vols=(0.25, 0.25),
        dividend_yields=(0.01, 0.01),
        correlation=((1.0, 0.5), (0.5, 1.0)),
        credit_spread=0.005,
    )
    r1 = price_note(ts, market, n_paths=20000, seed=123)
    r2 = price_note(ts, market, n_paths=20000, seed=123)
    assert r1.price == r2.price  # reproducible with the same seed
    assert r1.std_error is not None and r1.std_error > 0
    assert r1.measure == Measure.RISK_NEUTRAL
    assert r1.paths == 20000 and r1.seed == 123
    max_call = max(o.call_amount.value for o in ts.autocall.observations)
    max_total = max_call
    assert 0 < r1.price < max_total


def test_closed_form_single_asset_barrier():
    # One asset, no autocall/coupon, BARRIER FINAL_ONLY L=0.7 g=1 spread 0, T=1,
    # r=0.04 q=0.01 sigma=0.25: value = N*[exp(-rT)-P(0.7)-0.3*DP(0.7)], S=1.
    pricing = date(2025, 1, 1)
    final = date(2026, 1, 1)
    maturity = date(2026, 1, 1)  # 365 days -> T = 1.0 on Act/365
    ts = make_ts(
        n_assets=1,
        pricing=pricing,
        final=final,
        maturity=maturity,
        downside_level=0.7,
        gearing=1.0,
    )
    market = MarketInputs.single(
        as_of=pricing, risk_free_rate=0.04, vol=0.25, dividend_yield=0.01, credit_spread=0.0
    )
    S, K, T, r, q, sig, N = 1.0, 0.7, 1.0, 0.04, 0.01, 0.25, 1000.0
    put = bsm.put_price(S, K, T, r, sig, q)
    dig = bsm.digital_put_price(S, K, T, r, sig, q, cash=1.0)  # pays 1 if S_T < K
    expected = N * (np.exp(-r * T) - put - 0.3 * dig)
    res = price_note(ts, market, n_paths=200_000, seed=0, antithetic=True)
    assert abs(res.price - expected) <= 3 * res.std_error


def test_spread_lowers_price():
    ts = make_ts(n_assets=1, downside_level=0.7)
    pricing = date(2025, 1, 1)
    base = dict(risk_free_rate=0.04, vol=0.25, dividend_yield=0.01)
    m0 = MarketInputs.single(as_of=pricing, credit_spread=0.0, **base)
    m1 = MarketInputs.single(as_of=pricing, credit_spread=0.01, **base)
    r0 = price_note(ts, m0, n_paths=20000, seed=7)
    r1 = price_note(ts, m1, n_paths=20000, seed=7)
    assert r1.price < r0.price
