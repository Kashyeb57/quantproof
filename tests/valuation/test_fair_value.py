"""Tests for quantproof.valuation.fair_value.

Offline and deterministic: all pricing uses fixed seeds and the saved
hand-built JPM fixture from tests/conftest.py. No network or AI calls.

Tolerance notes are written next to every tolerance used, as required.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from quantproof.core.market import MarketInputs
from quantproof.core.termsheet import Cited
from quantproof.payoffs.worst_of import price_note
from quantproof.validation.battery import _barrier_at_maturity_setup
from quantproof.valuation.fair_value import (
    AssumptionGrid,
    fair_value_range,
    shifted_market,
    skew_vol,
)


def _market(ts):
    """The market specified in the task for the JPM fixture."""
    return MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17),
        dividend_yields=(0.012, 0.013),
        correlation=((1, 0.6), (0.6, 1)),
        credit_spread=0.006,
    )


def _full_grid() -> AssumptionGrid:
    return AssumptionGrid(
        (-0.05, 0, 0.05),
        (-0.25, 0, 0.25),
        (-0.0025, 0, 0.0025),
        (-0.005, 0, 0.005),
        (0, 0.02),
    )


# ---------------------------------------------------------------------------
# shifted_market
# ---------------------------------------------------------------------------


def test_shifted_market_correlation_blend_endpoints_and_midpoints(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)

    all_ones = shifted_market(market, correlation_shift=1.0)
    # Exact blend to the all-ones matrix; 1e-12 abs tolerance only allows
    # for floating-point representation of the stated 0.6 / 1.0 inputs.
    assert np.allclose(
        np.asarray(all_ones.correlation, dtype=float),
        np.ones((2, 2)),
        atol=1e-12,  # tolerance: exact convex-blend arithmetic, float round-off only
        rtol=0.0,
    )

    identity = shifted_market(market, correlation_shift=-1.0)
    assert np.allclose(
        np.asarray(identity.correlation, dtype=float),
        np.eye(2),
        atol=1e-12,  # tolerance: exact convex-blend arithmetic, float round-off only
        rtol=0.0,
    )

    up = shifted_market(market, correlation_shift=0.25)
    # (1 - 0.25) * 0.6 + 0.25 * 1 = 0.70.
    assert up.correlation[0][1] == pytest.approx(
        0.7, abs=1e-12  # tolerance: exact blend formula, float round-off only
    )
    assert up.correlation[1][0] == pytest.approx(
        0.7, abs=1e-12  # tolerance: exact blend formula, float round-off only
    )

    down = shifted_market(market, correlation_shift=-0.25)
    # (1 - 0.25) * 0.6 + 0.25 * 0 = 0.45.
    assert down.correlation[0][1] == pytest.approx(
        0.45, abs=1e-12  # tolerance: exact blend formula, float round-off only
    )
    assert down.correlation[1][0] == pytest.approx(
        0.45, abs=1e-12  # tolerance: exact blend formula, float round-off only
    )

    # Diagonals remain 1 under both blends.
    for shifted in (all_ones, identity, up, down):
        assert shifted.correlation[0][0] == pytest.approx(
            1.0, abs=1e-12  # tolerance: diagonal of a correlation matrix is exactly 1
        )
        assert shifted.correlation[1][1] == pytest.approx(
            1.0, abs=1e-12  # tolerance: diagonal of a correlation matrix is exactly 1
        )


@pytest.mark.parametrize("w", [-0.5, 0.5, 0.9])
def test_shifted_market_correlation_remains_psd_3x3(jpm_38582_termsheet, w):
    ts = jpm_38582_termsheet
    market3 = MarketInputs(
        as_of=ts.pricing_date.value,
        risk_free_rate=0.037,
        vols=(0.24, 0.17, 0.20),
        dividend_yields=(0.012, 0.013, 0.010),
        correlation=((1, 0.9, -0.4), (0.9, 1, -0.2), (-0.4, -0.2, 1)),
        credit_spread=0.006,
    )
    shifted = shifted_market(market3, correlation_shift=w)
    eigenvalues = np.linalg.eigvalsh(np.asarray(shifted.correlation, dtype=float))
    # A convex combination of PSD matrices is PSD, so eigenvalues are >= 0
    # mathematically; -1e-12 allows only numerical eigvalsh round-off.
    assert np.all(eigenvalues >= -1e-12)


def test_shifted_market_floors(jpm_38582_termsheet):
    market = _market(jpm_38582_termsheet)
    shifted = shifted_market(
        market,
        vol_shift=-10.0,
        spread_shift=-10.0,
        dividend_shift=-10.0,
    )
    # Floors are stated in the docstring: vol 0.01, dividend yield 0, spread 0.
    # These are exact max(...) floors, so equality (not a tolerance) is used.
    assert shifted.vols == (0.01, 0.01)
    assert shifted.dividend_yields == (0.0, 0.0)
    assert shifted.credit_spread == 0.0


@pytest.mark.parametrize("w", [1.000001, -1.000001, 2.0, -2.0])
def test_shifted_market_correlation_shift_out_of_range_raises(jpm_38582_termsheet, w):
    market = _market(jpm_38582_termsheet)
    with pytest.raises(ValueError):
        shifted_market(market, correlation_shift=w)


def test_shifted_market_does_not_mutate_caller(jpm_38582_termsheet):
    market = _market(jpm_38582_termsheet)
    before = copy.deepcopy(market)
    _ = shifted_market(
        market,
        vol_shift=0.05,
        correlation_shift=0.5,
        spread_shift=0.001,
        dividend_shift=0.001,
        skew_vol=0.03,
    )
    # Dataclass equality against a deepcopy taken before the call: this is
    # an exact no-mutation check, so no numerical tolerance is involved.
    assert market == before


# ---------------------------------------------------------------------------
# skew_vol
# ---------------------------------------------------------------------------


def test_skew_vol_at_fixture_barrier(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    # K = 0.7 in the fixture: 0.02 * (1 - 0.7) / 0.10 = 0.06.
    assert skew_vol(ts, 0.02) == pytest.approx(
        0.06, abs=1e-12  # tolerance: closed-form arithmetic, float round-off only
    )


def test_skew_vol_zero_at_or_above_initial(jpm_38582_termsheet):
    ts = copy.deepcopy(jpm_38582_termsheet)
    ts.downside.level = Cited(1.0)
    # max(0, 1 - K) = 0 for K >= 1, so the result is exactly 0.
    assert skew_vol(ts, 0.02) == 0.0
    ts.downside.level = Cited(1.0)
    assert skew_vol(ts, 0.05) == 0.0


# ---------------------------------------------------------------------------
# AssumptionGrid validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"vol_shifts": (0.05,)},
        {"correlation_shifts": (0.25,)},
        {"spread_shifts": (0.0025,)},
        {"dividend_shifts": (0.005,)},
        {"skew_per_10pct": (0.02,)},
        {"vol_shifts": (-0.05, 0.05)},
        {"skew_per_10pct": (0.01, 0.02)},
    ],
)
def test_assumption_grid_without_zero_raises(kwargs):
    with pytest.raises(ValueError):
        AssumptionGrid(**kwargs)


# ---------------------------------------------------------------------------
# fair_value_range on the JPM note
# ---------------------------------------------------------------------------


def test_fair_value_range_jpm(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    grid = _full_grid()
    seed = 0
    n_paths = 5_000
    n_paths_base = 20_000

    result = fair_value_range(
        ts,
        market,
        grid=grid,
        n_paths=n_paths,
        n_paths_base=n_paths_base,
        seed=seed,
    )

    # 3 vol * 3 correlation * 3 spread * 3 dividend * 2 skew = 162.
    assert len(result.points) == 162

    # low/high are defined as min/max over the points: exact identities,
    # so no numerical tolerance is involved.
    assert result.low <= result.high
    assert result.low == min(p.price for p in result.points)
    assert result.high == max(p.price for p in result.points)
    for point in result.points:
        # Every point lies in [low, high] by construction of min/max:
        # exact check, no tolerance.
        assert result.low <= point.price <= result.high

    # Sensitivity directions. Common random numbers (same seed for every
    # point) make these strict for the stated shift sizes: differences
    # reflect the assumption change, not independent simulation noise.
    # Hence strict inequalities with no tolerance are used.
    vol_low, vol_high = result.sensitivities["vol"]
    assert vol_high < vol_low

    corr_low, corr_high = result.sensitivities["correlation"]
    assert corr_high > corr_low

    spread_low, spread_high = result.sensitivities["spread"]
    assert spread_high < spread_low

    div_low, div_high = result.sensitivities["dividend"]
    assert div_high < div_low

    skew_zero, skew_002 = result.sensitivities["skew"]
    assert skew_002 < skew_zero

    # Bank estimate is the fixture's disclosed comparison point.
    assert result.bank_estimate == 954.0
    assert result.bank_position in ("below range", "inside range", "above range")
    # Consistency of bank_position with low/high is definitional: exact
    # comparisons, no tolerance.
    if result.bank_estimate < result.low:
        assert result.bank_position == "below range"
    elif result.bank_estimate > result.high:
        assert result.bank_position == "above range"
    else:
        assert result.bank_position == "inside range"

    assert result.preliminary == ts.is_preliminary.value
    assert any(
        "comparison point, not a calibration target" in line
        for line in result.assumptions
    )

    # JSON serialisability of the reported representation.
    encoded = json.dumps(result.to_dict())
    assert isinstance(encoded, str)

    # Base is priced at the unshifted inputs with n_paths_base and the same
    # seed. Repricing with identical inputs/seed/paths must reproduce it
    # exactly (deterministic Monte Carlo), so equality is used, not a
    # tolerance.
    expected_base = price_note(ts, market, n_paths=n_paths_base, seed=seed)
    assert result.base.price == expected_base.price
    assert result.base.std_error == expected_base.std_error
    assert result.base.seed == expected_base.seed
    assert result.base.paths == expected_base.paths


def test_bank_estimate_is_never_an_input(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    # Tiny grid: two values on one axis, only the base value (0.0,) on all
    # other axes. Every tuple contains 0.0 as AssumptionGrid requires.
    tiny_grid = AssumptionGrid(
        vol_shifts=(0.0, 0.05),
        correlation_shifts=(0.0,),
        spread_shifts=(0.0,),
        dividend_shifts=(0.0,),
        skew_per_10pct=(0.0,),
    )
    kwargs = dict(grid=tiny_grid, n_paths=5_000, n_paths_base=5_000, seed=0)

    original = fair_value_range(ts, market, **kwargs)

    changed_ts = copy.deepcopy(ts)
    changed_ts.estimated_value = Cited(500.0)
    changed = fair_value_range(changed_ts, market, **kwargs)

    # Changing only the disclosed bank estimate must not change any priced
    # quantity: same seed/inputs give bitwise-identical deterministic MC
    # output, so exact equality is used, not a tolerance.
    assert changed.low == original.low
    assert changed.high == original.high
    assert changed.points == original.points
    assert changed.base.price == original.base.price

    # Only the comparison fields differ.
    assert original.bank_estimate == 954.0
    assert changed.bank_estimate == 500.0
    # 500 is below any plausible low here, but assert consistency rather
    # than assuming a particular position: definitional exact comparisons.
    assert changed.bank_position in ("below range", "inside range", "above range")
    if changed.bank_estimate < changed.low:
        assert changed.bank_position == "below range"
    elif changed.bank_estimate > changed.high:
        assert changed.bank_position == "above range"
    else:
        assert changed.bank_position == "inside range"
    # The changed comparison must not accidentally equal the original one
    # unless both estimates genuinely fall in the same bucket; the
    # estimates themselves definitely differ.
    assert changed.bank_estimate != original.bank_estimate


def test_fair_value_range_one_asset_collapses_correlation_shifts(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    ts1, m1, _closed_form, _components = _barrier_at_maturity_setup(ts, market)

    grid = _full_grid()
    result = fair_value_range(
        ts1,
        m1,
        grid=grid,
        n_paths=5_000,
        n_paths_base=5_000,
        seed=0,
    )

    # Correlation shifts collapse to (0.0,) for N = 1, so the count is
    # 3 vol * 1 correlation * 3 spread * 3 dividend * 2 skew = 54, not 162.
    assert len(result.points) == 3 * 1 * 3 * 3 * 2
    assert len(result.points) == 54
    # Exact check: every point was priced with the sole correlation shift.
    assert all(p.correlation_shift == 0.0 for p in result.points)
    # With only one correlation point, low and high sensitivities coincide:
    # exact equality, no tolerance.
    corr_low, corr_high = result.sensitivities["correlation"]
    assert corr_low == corr_high


# ---------------------------------------------------------------------------
# Relative vol band for anchored legs
# ---------------------------------------------------------------------------


def test_shifted_market_relative_band_for_anchored_legs(jpm_38582_termsheet):
    market = _market(jpm_38582_termsheet)  # vols (0.24, 0.17)
    shifted = shifted_market(
        market, vol_shift=-0.05, vol_rel_shift=-0.15, vol_anchored=(True, False)
    )
    # Anchored leg scales: 0.24 * (1 - 0.15) = 0.204; the other leg keeps
    # the absolute shift: 0.17 - 0.05 = 0.12. Both are exact decimal
    # arithmetic, so 1e-12 allows only float round-off.
    assert shifted.vols[0] == pytest.approx(0.204, abs=1e-12)
    assert shifted.vols[1] == pytest.approx(0.12, abs=1e-12)
    assert "vol_anchored=(True, False)" in shifted.sources["grid"]


def test_shifted_market_vol_anchored_length_mismatch_raises(jpm_38582_termsheet):
    market = _market(jpm_38582_termsheet)
    with pytest.raises(ValueError):
        shifted_market(market, vol_anchored=(True,))


def test_fair_value_range_relative_band_narrows_and_ignores_bank(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    grid = AssumptionGrid(
        vol_shifts=(-0.05, 0.0, 0.05),
        correlation_shifts=(0.0,),
        spread_shifts=(0.0,),
        dividend_shifts=(0.0,),
        skew_per_10pct=(0.0,),
    )
    kwargs = dict(grid=grid, n_paths=5_000, n_paths_base=5_000, seed=0)
    absolute = fair_value_range(ts, market, **kwargs)
    anchored = fair_value_range(ts, market, vol_anchored=(True, True), **kwargs)
    # 3 vol points each; anchored points record the relative shift.
    assert [p.vol_shift for p in absolute.points] == [-0.05, 0.0, 0.05]
    assert [p.vol_shift for p in anchored.points] == [-0.15, 0.0, 0.15]
    # The ±15% relative band sits inside the ±5-point absolute band on both
    # legs, and value falls as vol rises (short-put note), so the anchored
    # range nests inside the absolute one. Common random numbers keep noise
    # far below these moves, so strict inequalities are used, as above.
    assert anchored.low > absolute.low
    assert anchored.high < absolute.high
    # The assumptions state why: implied vol observed on the pricing date.
    assert any("±15% relative band" in line for line in anchored.assumptions)
    assert anchored.to_dict()["grid"]["vol_rel_shifts"] == [-0.15, 0.0, 0.15]
    # The bank's number never chooses the band: a different disclosed
    # estimate reprices identically (deterministic MC, exact equality).
    changed_ts = copy.deepcopy(ts)
    changed_ts.estimated_value = Cited(500.0)
    changed = fair_value_range(changed_ts, market, vol_anchored=(True, True), **kwargs)
    assert changed.low == anchored.low
    assert changed.high == anchored.high
    assert changed.points == anchored.points


def test_fair_value_range_mismatched_vol_triples_raise(jpm_38582_termsheet):
    ts = jpm_38582_termsheet
    market = _market(ts)
    grid = AssumptionGrid(
        vol_shifts=(0.0,),
        correlation_shifts=(0.0,),
        spread_shifts=(0.0,),
        dividend_shifts=(0.0,),
        skew_per_10pct=(0.0,),
    )
    with pytest.raises(ValueError):
        fair_value_range(ts, market, grid=grid, vol_anchored=(True, True))
