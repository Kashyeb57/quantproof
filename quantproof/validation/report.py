"""Validation report rendering: pure SVG charts and Markdown (no I/O).

This module turns proof-battery ``CheckResult`` objects into hand-written
SVG strings and a Markdown report.  It performs no file or network I/O and
uses no plotting library: every chart is built explicitly below so that a
reviewer can inspect exactly how each point, bar and error bar is drawn.

Pure Python only (``html``, ``inspect``, ``math``).  No AI and no network
calls in this module.

References: proof-battery checks in ``quantproof.validation.battery``;
J. C. Hull, *Options, Futures, and Other Derivatives*, chapters 15, 21 and
26 (closed forms, simulation and barrier payoffs); P. Glasserman,
*Monte Carlo Methods in Financial Engineering*, chapters 2-3 (standard
errors).
"""

from __future__ import annotations

import html
import inspect
import math
from typing import Any

ACCENT = "#2563eb"
GREY = "#6b7280"
RED = "#dc2626"
WIDTH = 640
HEIGHT = 360

# Fallback one-line explanations, used only if a check function cannot be
# imported (normally the wording is taken from the check's own docstring,
# as required, by ``_check_intuition`` below).
_FALLBACK_INTUITIONS: dict[str, str] = {
    "bsm_known_values": "A call is the present value of receiving the stock when it ends above the strike minus paying the strike in those states; the put mirrors it.",
    "put_call_parity": "A call minus a put pays S_T - K in every state, exactly like holding the (dividend-adjusted) stock and owing discounted cash K.",
    "determinism": "A fixed seed fixes every random draw, so a reported price can always be recomputed by a reviewer.",
    "barrier_removed": "With the buffer at 100% the investor gives up every gain and bears every loss from the first cent, i.e. is short an at-the-money put.",
    "barrier_at_maturity": "Principal is repaid unless the asset ends below the barrier, in which case the investor loses 1 - W: a put plus a digital for the gap.",
    "never_callable": "A trigger of 1e6 can never be reached, so the note certainly survives to maturity and every coupon is an independent digital bet.",
    "zero_coupon_no_downside": "With no coupon, no upside and no realistic downside, all that remains is repayment of principal, discounted with the issuer's spread.",
    "zero_vol": "With no randomness there is nothing to simulate; the engine must reproduce the single deterministic path in both states of the world.",
    "correlation_one": "The worst of two identical performances is that performance, so adding a perfect copy cannot change the payoff on any path.",
    "correlation_monotonicity": "Higher correlation makes the underlyings fall together, so the worst of them is less likely to be far below the others; a short worst-of downside position is therefore worth more.",
    "vol_monotonicity": "With no upside leg the note is short downside optionality, so more volatility raises the value of what the investor has sold.",
    "spread_monotonicity": "The note is the issuer's unsecured debt; a wider spread is a bigger discount on every promised payment.",
    "coupon_monotonicity": "The investor is long the coupon leg; paying 1.5x on the same paths pays more on every path that pays at all.",
    "convergence": "The standard error of a Monte Carlo mean falls with the square root of the number of paths; this checks the reported SE is honest.",
    "quotes": "A number without a quote that can be found in the filing cannot be trusted or shown to a user silently (project rule 5a).",
}


def _esc(value: Any) -> str:
    """Escape text or an attribute value for inclusion in SVG / XML."""
    return html.escape(str(value), quote=True)


def _fmt2(value: float) -> str:
    """Format a chart value to 2 decimals."""
    return f"{float(value):.2f}"


def _fmt_num(value: float | None) -> str:
    """Markdown number format: 2 decimals, 4 below 1 in absolute terms, '-' for None.

    Formula: no formula; this is a display rule stated in the task.

    Intuition: small quantities (rates-like tolerances and ratios) need more
    decimals to be distinguishable from zero, while prices are read to cents.

    Reference: task specification for ``render_markdown``.
    """
    if value is None:
        return "-"
    v = float(value)
    if abs(v) < 1:
        return f"{v:.4f}"
    return f"{v:.2f}"


def _svg_open(title: str) -> list[str]:
    """Opening ``<svg>`` tag plus the accessible ``<title>`` element."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" font-family="sans-serif" role="img">',
        f"<title>{_esc(title)}</title>",
    ]


def _visible_title(title: str) -> str:
    return (
        f'<text x="{WIDTH / 2:.1f}" y="24" text-anchor="middle" '
        f'font-size="16" fill="#111827">{_esc(title)}</text>'
    )


def _axis_labels(x_label: str, y_label: str, left: float, top: float, plot_h: float) -> list[str]:
    """Labelled axes: x label centred under the plot, y label rotated."""
    mid_x = left + (WIDTH - 20 - left) / 2
    y_mid = top + plot_h / 2
    return [
        f'<text x="{mid_x:.1f}" y="{HEIGHT - 8}" text-anchor="middle" '
        f'font-size="12" fill="{GREY}">{_esc(x_label)}</text>',
        f'<text x="16" y="{y_mid:.1f}" text-anchor="middle" font-size="12" '
        f'fill="{GREY}" transform="rotate(-90 16 {y_mid:.1f})">{_esc(y_label)}</text>',
    ]


def svg_convergence(rows: list[dict]) -> str:
    """SVG line chart of Monte Carlo price against number of paths (log x).

    Formula: x is ``log10(paths)``; each point is ``price`` with an error bar
    of ``price +/- 3 * se``; the horizontal dashed line is ``closed_form``
    from the first row that carries it.

    Intuition: as paths quadruple the standard error should halve, and the
    price should settle on the independent closed form.

    Reference: Glasserman chapters 2 and 4 (Monte Carlo standard errors);
    task specification (640x360, one accent colour, no external references).
    """
    title = "Monte Carlo convergence"
    parts = _svg_open(title)
    parts.append(_visible_title(title))
    if not rows:
        parts.append(
            f'<text x="{WIDTH / 2:.1f}" y="{HEIGHT / 2:.1f}" text-anchor="middle" '
            f'font-size="12" fill="{GREY}">no data</text>'
        )
        parts.append("</svg>")
        return "\n".join(parts)

    left, right, top, bottom = 70.0, 20.0, 40.0, 50.0
    plot_w = WIDTH - left - right
    plot_h = HEIGHT - top - bottom

    xs_log = [math.log10(float(r["paths"])) for r in rows]
    x_min, x_max = min(xs_log), max(xs_log)
    if x_max == x_min:
        x_min -= 0.5
        x_max += 0.5
    lows = [float(r["price"]) - 3.0 * float(r.get("se", 0.0)) for r in rows]
    highs = [float(r["price"]) + 3.0 * float(r.get("se", 0.0)) for r in rows]
    closed = None
    for r in rows:
        if r.get("closed_form") is not None:
            closed = float(r["closed_form"])
            break
    y_candidates = lows + highs + ([closed] if closed is not None else [])
    y_min, y_max = min(y_candidates), max(y_candidates)
    if y_max == y_min:
        y_min -= 1.0
        y_max += 1.0
    pad = 0.08 * (y_max - y_min)
    y_min -= pad
    y_max += pad

    def x_px(v: float) -> float:
        return left + (v - x_min) / (x_max - x_min) * plot_w

    def y_px(v: float) -> float:
        return top + (y_max - v) / (y_max - y_min) * plot_h

    # Axes.
    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{GREY}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{GREY}" stroke-width="1"/>'
    )
    # Y ticks (5).
    for i in range(5):
        v = y_min + i * (y_max - y_min) / 4
        y = y_px(v)
        parts.append(
            f'<line x1="{left - 4}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" stroke="{GREY}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" fill="{GREY}">{_esc(_fmt2(v))}</text>'
        )
    # X ticks, one per row, labelled with the path count.
    for r, lx in zip(rows, xs_log):
        x = x_px(lx)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" y2="{top + plot_h + 4}" stroke="{GREY}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 18:.1f}" text-anchor="middle" font-size="10" fill="{GREY}">{_esc(int(r["paths"]))}</text>'
        )
    # Closed-form dashed line.
    if closed is not None:
        y = y_px(closed)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{ACCENT}" stroke-width="1.5" stroke-dasharray="6 4"/>'
        )
        parts.append(
            f'<text x="{left + plot_w - 4}" y="{y - 6:.1f}" text-anchor="end" font-size="10" fill="{ACCENT}">{_esc("closed form " + _fmt2(closed))}</text>'
        )
    # Error bars (+-3 SE) and the connecting line plus markers.
    points: list[tuple[float, float]] = []
    for r, lx in zip(rows, xs_log):
        x = x_px(lx)
        price = float(r["price"])
        se = float(r.get("se", 0.0))
        y = y_px(price)
        points.append((x, y))
        y_lo, y_hi = y_px(price - 3.0 * se), y_px(price + 3.0 * se)
        parts.append(
            f'<line x1="{x:.1f}" y1="{y_lo:.1f}" x2="{x:.1f}" y2="{y_hi:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<line x1="{x - 5:.1f}" y1="{y_lo:.1f}" x2="{x + 5:.1f}" y2="{y_lo:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<line x1="{x - 5:.1f}" y1="{y_hi:.1f}" x2="{x + 5:.1f}" y2="{y_hi:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
        )
    if len(points) > 1:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(f'<polyline points="{_esc(pts)}" fill="none" stroke="{ACCENT}" stroke-width="2"/>')
    for x, y in points:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}"/>')
    parts.extend(_axis_labels("paths (log scale)", "price", left, top, plot_h))
    parts.append("</svg>")
    return "\n".join(parts)


def svg_decomposition(data: dict) -> str:
    """SVG horizontal waterfall chart of the barrier-note decomposition.

    Formula: ``closed_form = bond + put + digital`` where ``put`` and
    ``digital`` are negative components; each negative bar starts where the
    previous bar ended.  The Monte Carlo bar carries a ``+/- 3 * se``
    whisker.

    Intuition: the note is a bond with a put and a digital sold against it,
    so the closed-form total is what remains after subtracting both.

    Reference: Hull chapters 15, 19 and 26; task specification.
    """
    title = "Barrier note = bond - put - digital"
    parts = _svg_open(title)
    parts.append(_visible_title(title))

    bond = float(data.get("bond", 0.0))
    put = float(data.get("put", 0.0))
    digital = float(data.get("digital", 0.0))
    closed = float(data.get("closed_form", bond + put + digital))
    mc = float(data.get("mc", closed))
    se = float(data.get("se", 0.0))

    # (label, start, end, colour) - waterfall segments then totals from zero.
    segments: list[tuple[str, float, float, str]] = [
        ("bond", 0.0, bond, ACCENT),
        ("put", bond, bond + put, RED),
        ("digital", bond + put, bond + put + digital, RED),
        ("closed form", 0.0, closed, ACCENT),
        ("MC", 0.0, mc, ACCENT),
    ]
    values = [0.0, bond, bond + put, bond + put + digital, closed, mc - 3.0 * se, mc + 3.0 * se]
    x_min, x_max = min(values), max(values)
    if x_max == x_min:
        x_max = x_min + 1.0
    pad = 0.08 * (x_max - x_min)
    x_min -= pad
    x_max += pad

    left, right, top = 110.0, 90.0, 46.0
    plot_w = WIDTH - left - right
    row_h = 52.0
    bar_h = 26.0

    def x_px(v: float) -> float:
        return left + (v - x_min) / (x_max - x_min) * plot_w

    # Zero axis and x ticks.
    zero_x = x_px(0.0)
    bottom = top + row_h * len(segments)
    parts.append(
        f'<line x1="{zero_x:.1f}" y1="{top - 6}" x2="{zero_x:.1f}" y2="{bottom:.1f}" stroke="{GREY}" stroke-width="1"/>'
    )
    for i in range(5):
        v = x_min + i * (x_max - x_min) / 4
        x = x_px(v)
        parts.append(
            f'<text x="{x:.1f}" y="{bottom + 18:.1f}" text-anchor="middle" font-size="10" fill="{GREY}">{_esc(_fmt2(v))}</text>'
        )
    parts.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{HEIGHT - 8}" text-anchor="middle" font-size="12" fill="{GREY}">{_esc("value")}</text>'
    )

    for idx, (label, start, end, colour) in enumerate(segments):
        y = top + idx * row_h
        x1, x2 = x_px(min(start, end)), x_px(max(start, end))
        parts.append(
            f'<text x="{left - 8}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end" font-size="12" fill="#111827">{_esc(label)}</text>'
        )
        parts.append(
            f'<rect x="{x1:.1f}" y="{y:.1f}" width="{max(x2 - x1, 1.0):.1f}" height="{bar_h}" fill="{colour}"/>'
        )
        # Value of this component / total, to 2 decimals, at the bar's end.
        shown = end - start if idx < 3 else end
        label_x = x2 + 6 if end >= start else x1 - 6
        anchor = "start" if end >= start else "end"
        parts.append(
            f'<text x="{label_x:.1f}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="{anchor}" font-size="11" fill="#111827">{_esc(_fmt2(shown))}</text>'
        )
        if label == "MC":
            cy = y + bar_h / 2
            x_lo, x_hi = x_px(mc - 3.0 * se), x_px(mc + 3.0 * se)
            parts.append(
                f'<line x1="{x_lo:.1f}" y1="{cy:.1f}" x2="{x_hi:.1f}" y2="{cy:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<line x1="{x_lo:.1f}" y1="{cy - 6:.1f}" x2="{x_lo:.1f}" y2="{cy + 6:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<line x1="{x_hi:.1f}" y1="{cy - 6:.1f}" x2="{x_hi:.1f}" y2="{cy + 6:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def svg_series(rows, x_key, y_key, title, x_label) -> str:
    """SVG line-and-marker chart with ``+/- 2 * se`` bars for a sweep.

    Formula: x is ``row[x_key]``, y is ``row[y_key]``, and each error bar is
    ``y +/- 2 * row["se"]`` (rows without ``se`` get no bar).

    Intuition: a sweep (correlation, volatility, ...) should move the price
    monotonically; the 2 SE bars show whether an apparent step is within
    simulation noise.

    Reference: Glasserman chapters 2-3; task specification.
    """
    parts = _svg_open(title)
    parts.append(_visible_title(title))
    if not rows:
        parts.append(
            f'<text x="{WIDTH / 2:.1f}" y="{HEIGHT / 2:.1f}" text-anchor="middle" '
            f'font-size="12" fill="{GREY}">no data</text>'
        )
        parts.append("</svg>")
        return "\n".join(parts)

    left, right, top, bottom = 70.0, 20.0, 40.0, 50.0
    plot_w = WIDTH - left - right
    plot_h = HEIGHT - top - bottom
    xs = [float(r[x_key]) for r in rows]
    ys = [float(r[y_key]) for r in rows]
    lows = [y - 2.0 * float(r.get("se", 0.0)) for r, y in zip(rows, ys)]
    highs = [y + 2.0 * float(r.get("se", 0.0)) for r, y in zip(rows, ys)]
    x_min, x_max = min(xs), max(xs)
    if x_max == x_min:
        x_min -= 0.5
        x_max += 0.5
    y_min, y_max = min(lows), max(highs)
    if y_max == y_min:
        y_min -= 1.0
        y_max += 1.0
    pad = 0.08 * (y_max - y_min)
    y_min -= pad
    y_max += pad

    def x_px(v: float) -> float:
        return left + (v - x_min) / (x_max - x_min) * plot_w

    def y_px(v: float) -> float:
        return top + (y_max - v) / (y_max - y_min) * plot_h

    parts.append(
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{GREY}" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{GREY}" stroke-width="1"/>'
    )
    for i in range(5):
        v = y_min + i * (y_max - y_min) / 4
        y = y_px(v)
        parts.append(
            f'<line x1="{left - 4}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" stroke="{GREY}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" fill="{GREY}">{_esc(_fmt2(v))}</text>'
        )
    for r, x_val in zip(rows, xs):
        x = x_px(x_val)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" y2="{top + plot_h + 4}" stroke="{GREY}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 18:.1f}" text-anchor="middle" font-size="10" fill="{GREY}">{_esc(_fmt_num(x_val))}</text>'
        )
    points: list[tuple[float, float]] = []
    for r, x_val, y_val in zip(rows, xs, ys):
        x, y = x_px(x_val), y_px(y_val)
        points.append((x, y))
        se = float(r.get("se", 0.0))
        if se:
            y_lo, y_hi = y_px(y_val - 2.0 * se), y_px(y_val + 2.0 * se)
            parts.append(
                f'<line x1="{x:.1f}" y1="{y_lo:.1f}" x2="{x:.1f}" y2="{y_hi:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<line x1="{x - 5:.1f}" y1="{y_lo:.1f}" x2="{x + 5:.1f}" y2="{y_lo:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<line x1="{x - 5:.1f}" y1="{y_hi:.1f}" x2="{x + 5:.1f}" y2="{y_hi:.1f}" stroke="{GREY}" stroke-width="1.5"/>'
            )
    if len(points) > 1:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(f'<polyline points="{_esc(pts)}" fill="none" stroke="{ACCENT}" stroke-width="2"/>')
    for x, y in points:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{ACCENT}"/>')
    parts.extend(_axis_labels(str(x_label), str(y_key), left, top, plot_h))
    parts.append("</svg>")
    return "\n".join(parts)


def _check_intuition(name: str) -> str:
    """Plain-English intuition for check ``name``, from its docstring.

    The wording is taken from the ``Intuition:`` paragraph of the matching
    ``check_<name>`` function in ``quantproof.validation.battery``; a local
    fallback table is used only if that import or parse fails.
    """
    try:
        from quantproof.validation import battery

        fn = getattr(battery, f"check_{name}", None)
        if fn is not None:
            doc = inspect.getdoc(fn) or ""
            lines = doc.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("Intuition:"):
                    collected = [line.strip()[len("Intuition:") :].strip()]
                    for nxt in lines[i + 1 :]:
                        if not nxt.strip():
                            break
                        collected.append(nxt.strip())
                    text = " ".join(p for p in collected if p).strip()
                    if text:
                        return text
    except Exception:
        pass
    return _FALLBACK_INTUITIONS.get(name, "See the check docstring in quantproof/validation/battery.py.")


def render_markdown(notes: list[dict], generated: str) -> str:
    """Render the validation report as Markdown (pure function, no I/O).

    Formula: no pricing formula; this formats ``CheckResult`` values that
    engines have already produced.  Numbers use :func:`_fmt_num` (2 decimals,
    4 for values below 1 in absolute terms, ``-`` for ``None``).

    Intuition: the report shows, for each note, exactly what was compared
    with what and to what tolerance, so a reader can judge the proof without
    trusting any single price.

    Reference: task specification; project rules 3, 5, 5a and 8 (provenance,
    no calibration to a bank estimate, flagged unverified fields, no advice).
    """
    lines: list[str] = []
    lines.append("# Validation report")
    lines.append("")
    lines.append(
        "This report records what the proof battery proves: the Monte Carlo engine "
        "reproduces independent closed forms, textbook values, deterministic limits "
        "and economic monotonicity properties on notes extracted from the filings. "
        "Inputs are illustrative assumptions for testing the engine, not a valuation "
        "and not calibrated to any bank estimate; any bank estimated value shown is "
        "a comparison point only. Generated by scripts/validation_report.py at "
        f"{generated}."
    )
    lines.append("")

    # Summary table.
    lines.append("## Summary")
    lines.append("")
    lines.append("| Note | Checks passed | Flagged |")
    lines.append("| --- | --- | --- |")
    for note in notes:
        label = str(note.get("label", note.get("file", "")))
        if note.get("refused"):
            lines.append(f"| {label} | - | refused |")
            continue
        results = note.get("results") or []
        n_passed = sum(1 for r in results if r.passed)
        flagged = any(r.name == "quotes" and not r.passed for r in results)
        lines.append(
            f"| {label} | {n_passed}/{len(results)} | {'FLAGGED' if flagged else '-'} |"
        )
    lines.append("")

    seen_checks: list[str] = []
    for note in notes:
        label = str(note.get("label", note.get("file", "")))
        lines.append(f"## {label}")
        lines.append("")
        if note.get("refused"):
            lines.append(f"Refused: {note['refused']}")
            lines.append("")
            continue
        if note.get("termsheet_summary"):
            lines.append(str(note["termsheet_summary"]))
            lines.append("")
        if note.get("market_summary"):
            lines.append(str(note["market_summary"]))
            lines.append("")
        results = note.get("results") or []
        lines.append("| Check | Result | Engine | Reference | Tolerance | What was compared |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in results:
            if r.name not in seen_checks:
                seen_checks.append(r.name)
            if r.name == "quotes" and not r.passed:
                status = "FLAGGED"
            else:
                status = "PASS" if r.passed else "FAIL"
            detail = str(r.detail).replace("|", "\\|")
            lines.append(
                f"| {r.name} | {status} | {_fmt_num(r.value)} | {_fmt_num(r.reference)} "
                f"| {_fmt_num(r.tolerance)} | {detail} |"
            )
        lines.append("")
        for r in results:
            if r.name == "quotes" and not r.passed:
                unverified = (r.data or {}).get("unverified") or []
                lines.append(
                    "Unverified fields are flagged to the user, not a pricing failure. "
                    "Unverified:"
                )
                lines.append("")
                for path in unverified:
                    lines.append(f"- {path}")
                if not unverified:
                    lines.append("- (none listed)")
                lines.append("")
        charts = note.get("charts") or {}
        for chart_name, chart_path in charts.items():
            lines.append(f"![{chart_name}]({chart_path})")
            lines.append("")

    lines.append("## Checks")
    lines.append("")
    if seen_checks:
        for name in seen_checks:
            lines.append(f"- **{name}**: {_check_intuition(name)}")
    else:
        # Still explain every known check if no note supplied results.
        for name in _FALLBACK_INTUITIONS:
            lines.append(f"- **{name}**: {_check_intuition(name)}")
    lines.append("")
    return "\n".join(lines)
