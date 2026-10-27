"""Correlated geometric Brownian motion simulation and Monte Carlo statistics.

Model: for each asset i, under the caller-labelled measure,
    dS_i / S_i = (mu_i - q_i) dt + sigma_i dW_i,
with per-asset drift mu_i, dividend yield q_i, volatility sigma_i, and Brownian
motions with dW_i dW_j = rho_ij dt. Between observation dates the exact solution is
    ln R_i(t_k) = ln R_i(t_{k-1}) + (mu_i - q_i - sigma_i^2 / 2) dt
                  + sigma_i sqrt(dt) (L z)_i,  z ~ N(0, I),
where R_i(t) = S_i(t) / S_i(0) and L L^T = correlation. Pass mu_i = r for the
risk-neutral measure (value) or a stated real-world drift for loss odds; the caller
labels the measure.

Pure Python + NumPy. No AI and no network calls in this module.

References: J. C. Hull, *Options, Futures, and Other Derivatives*, chapter 21
(basic numerical procedures); P. Glasserman, *Monte Carlo Methods in Financial
Engineering*, chapters 2-3 (normal sampling, correlated normals, antithetic variates).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def correlation_factor(corr: np.ndarray) -> np.ndarray:
    """Lower factor L of a correlation matrix with L @ L.T == corr.

    Formula: Cholesky factor L (corr = L L^T); if Cholesky fails for a singular
    positive semi-definite matrix (for example correlation 1), the eigen factor
    V diag(sqrt(max(lam, 0))) from corr = V diag(lam) V^T is used instead.

    Intuition: independent normals z become correlated via L z, which then has
    covariance L L^T = corr. This is how one joint shock is built from independent ones.

    Raises ValueError if corr is not square, not symmetric, has a diagonal entry
    != 1, or has an eigenvalue < -1e-10 (not positive semi-definite).
    """
    c = np.asarray(corr, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("correlation must be a square matrix")
    if c.shape[0] == 0:
        raise ValueError("correlation must be non-empty")
    if not np.allclose(c, c.T, rtol=0, atol=1e-12):
        raise ValueError("correlation matrix must be symmetric")
    if not np.allclose(np.diag(c), 1.0, rtol=0, atol=1e-12):
        raise ValueError("correlation matrix must have ones on the diagonal")
    smallest = float(np.linalg.eigvalsh(c).min())
    if smallest < -1e-10:
        raise ValueError("correlation matrix must be positive semi-definite")
    try:
        return np.linalg.cholesky(c)
    except np.linalg.LinAlgError:
        # Singular but still PSD (for example all-ones 2x2): fall back to the
        # symmetric eigen factor. Clip tiny negatives from rounding to zero.
        values, vectors = np.linalg.eigh(c)
        values = np.maximum(values, 0.0)
        return vectors * np.sqrt(values)[np.newaxis, :]


def simulate_performance(
    times: Sequence[float] | np.ndarray,
    vols: Sequence[float] | np.ndarray,
    dividend_yields: Sequence[float] | np.ndarray,
    correlation: np.ndarray,
    drifts: Sequence[float] | np.ndarray,
    n_paths: int,
    seed: int,
    antithetic: bool = True,
) -> np.ndarray:
    """Simulate performance ratios R_i(t) = S_i(t) / S_i(0), shape (n_paths, len(times), n_assets).

    Formula (exact log-normal step between observation dates only, no finer grid):
    ln R(t_k) = ln R(t_{k-1}) + (mu_i - q_i - sigma_i^2/2) dt
                + sigma_i sqrt(dt) (L z)_i,  z ~ N(0, I).

    Intuition: geometric Brownian motion has an exact solution, so we step directly
    from one observation date to the next; correlating the normals with L reproduces
    the assets' joint moves without simulating every day in between.

    Antithetic sampling draws n_paths / 2 normal blocks and reuses them as z and
    -z; rows 0..n/2-1 hold the z paths and row i + n/2 is the antithetic partner
    of row i, which cancels symmetric sampling noise.

    Raises ValueError if times are not strictly increasing with all entries > 0,
    if the per-asset input lengths disagree with the correlation shape, if any
    vol < 0, or if n_paths is not a positive even number when antithetic is True.
    """
    times_arr = np.asarray(times, dtype=float)
    vols_arr = np.asarray(vols, dtype=float)
    divs_arr = np.asarray(dividend_yields, dtype=float)
    drifts_arr = np.asarray(drifts, dtype=float)

    if times_arr.ndim != 1 or times_arr.size == 0:
        raise ValueError("times must be a non-empty one-dimensional sequence")
    if np.any(times_arr <= 0):
        raise ValueError("times must all be positive year fractions")
    if not np.all(np.diff(times_arr) > 0):
        raise ValueError("times must be strictly increasing")

    if vols_arr.ndim != 1 or divs_arr.ndim != 1 or drifts_arr.ndim != 1:
        raise ValueError("vols, dividend_yields and drifts must be one-dimensional")
    n_assets = vols_arr.shape[0]
    if n_assets == 0:
        raise ValueError("need at least one underlying")
    if divs_arr.shape[0] != n_assets or drifts_arr.shape[0] != n_assets:
        raise ValueError("vols, dividend_yields and drifts must have the same length")
    if np.any(vols_arr < 0):
        raise ValueError("volatilities must be non-negative")

    if not isinstance(n_paths, (int, np.integer)) or int(n_paths) <= 0:
        raise ValueError("n_paths must be a positive integer")
    n_paths = int(n_paths)
    if antithetic and n_paths % 2 != 0:
        raise ValueError("n_paths must be even when antithetic is True")

    factor = correlation_factor(correlation)
    if factor.shape != (n_assets, n_assets):
        raise ValueError(
            f"correlation must be {n_assets} x {n_assets} for {n_assets} assets"
        )

    n_times = times_arr.shape[0]
    dts = np.empty(n_times, dtype=float)
    dts[0] = times_arr[0]
    dts[1:] = np.diff(times_arr)
    sqrt_dts = np.sqrt(dts)
    drift_vec = drifts_arr - divs_arr - 0.5 * vols_arr**2

    rng = np.random.default_rng(seed)
    if antithetic:
        half = n_paths // 2
        z_half = rng.standard_normal(size=(half, n_times, n_assets))
        normals = np.empty((n_paths, n_times, n_assets), dtype=float)
        normals[:half] = z_half
        normals[half:] = -z_half
    else:
        normals = rng.standard_normal(size=(n_paths, n_times, n_assets))

    correlated = normals @ factor.T
    drift_inc = drift_vec[np.newaxis, np.newaxis, :] * dts[np.newaxis, :, np.newaxis]
    diffusion_inc = (
        vols_arr[np.newaxis, np.newaxis, :]
        * sqrt_dts[np.newaxis, :, np.newaxis]
        * correlated
    )
    log_ratio = np.cumsum(drift_inc + diffusion_inc, axis=1)
    return np.exp(log_ratio)


def mc_mean_and_se(samples: np.ndarray, antithetic: bool) -> tuple[float, float]:
    """Mean and standard error of Monte Carlo samples.

    Formula without antithetic: se = std(samples, ddof=1) / sqrt(n). With
    antithetic: form pair means m_j = (x_j + x_{j+n/2}) / 2 and report
    se = std(m, ddof=1) / sqrt(n/2).

    Intuition: the two paths of an antithetic pair share the same normals, so they are
    not independent; the independent unit is the pair average. Treating paired paths as
    independent would misstate the error (overstate it for the usual negatively
    correlated pair, understate it for a payoff where the pair is positively correlated).

    Raises ValueError if samples are not one-dimensional, if there are fewer than
    2 observations (fewer than 2 pairs when antithetic), or if the length is odd
    when antithetic is True.
    """
    arr = np.asarray(samples, dtype=float)
    if arr.ndim != 1:
        raise ValueError("samples must be one-dimensional")
    n = arr.shape[0]
    if not antithetic:
        if n < 2:
            raise ValueError("need at least 2 samples")
        return float(np.mean(arr)), float(np.std(arr, ddof=1) / np.sqrt(n))
    if n % 2 != 0:
        raise ValueError("antithetic samples must have an even length")
    n_pairs = n // 2
    if n_pairs < 2:
        raise ValueError("need at least 2 antithetic pairs")
    pair_means = (arr[:n_pairs] + arr[n_pairs:]) / 2.0
    return float(np.mean(pair_means)), float(np.std(pair_means, ddof=1) / np.sqrt(n_pairs))
