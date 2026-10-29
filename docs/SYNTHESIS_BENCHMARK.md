# Synthesis benchmark

## Method

Templates are disabled for synthesis: for every note in this benchmark, the selected model writes the full payoff code from the filing text and the extracted terms: --model wonder (the default, Muse Wonder 1.0 via the Meta API) or --model open (Muse Spark Open via Baseten, key from BASETEN_API_KEY, final answer only).  Open-model spend is Baseten credits, billed apart from Meta spend, and each saved payoff records which model wrote it.  The generated code is untrusted: it runs only in a subprocess sandbox with a timeout, no network, and an import allow-list (numpy, scipy, math).

The proof battery checks are: convergence, determinism, filing_examples, output_sanity, static_safety.

A failed attempt gets up to 3 repairs.  Each repair is fed the failing check name and a concrete counterexample produced by the battery, so the model sees the exact input on which its code disagreed with the filing or the stated payoff rules.

Pricing uses stated assumptions, not market data as of each pricing date.  Volatility is a flat vol under those stated assumptions and is labelled as such; no volatility skew is fitted.  Synthesized ranges exclude skew (the saved fair_value records this as "skew": "not included"), so synthesized ranges are narrower than template ranges on that point alone.  Fair value is reported as a range under the stated assumptions.  The bank's estimated value is a comparison point only and is never a calibration target.

## Coverage

Templates priced 11 of 42 extracted notes; with proven AI-written payoffs, 17 of 42 (8 of the 29 unsupported notes were attempted).

## Results

- Notes attempted: 10
- Notes passed: 9
- Pass rate: 90.0%
- Passed on the first attempt: 8
- Repaired after a failed attempt: 1
- Failed: 1
- Mean attempts per note: 1.10
- Total model cost (USD): 1.3293
- Cost (muse-wonder-1.0, USD): 1.3293
- 'open' costs are Baseten credits, billed separately from Meta credits.
- Total model seconds: 272.7
- Status counts: needs_review: 1, priced_ai: 8, synthesis_failed: 1

## Attempts per note

| CUSIP | Structure | Model | Attempts | First failing checks | Status | Cost ($) | Time (s) | Base value ± SE ($) | Range ($) | Bank estimate ($) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 89115NKY9 | single underlying, upside leverage 1.25x, cap 28.65%, buffer 70% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1790 | 30.0 | 991.81 ± 0.80 | 954.55 - 1022.16 | 983.60 |
| 40058LYL8 | single underlying, upside leverage 3x, cap 21.2%, barrier 100% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1211 | 25.1 | 942.12 ± 0.50 | 911.60 - 973.57 | 969.00 |
| 40058MG51 | single underlying, upside leverage 2.5x, cap 110%, barrier 100% at maturity | muse-wonder-1.0 | 1 |  | needs_review | 0.1381 | 22.4 | 1053.43 ± 0.48 | 1011.42 - 1094.43 | 962.00 |
| 46661QNW1 | single underlying, upside leverage 1x, cap 14.6%, buffer 90% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1374 | 36.9 | 1000.72 ± 0.28 | 987.52 - 1011.37 | 995.30 |
| 06376MPD0 | single underlying, buffer 85% at maturity | muse-wonder-1.0 | 2 | filing_examples | priced_ai | 0.1737 | 30.0 | 968.96 ± 1.06 | 920.99 - 1018.28 | 997.53 |
| 61781LSA9 | single underlying, upside leverage 1.5x, cap 22.2%, buffer 90% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1288 | 28.8 | 964.45 ± 0.44 | 935.81 - 991.70 | 972.80 |
| 06419TE79 | single underlying, buffer 85% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1372 | 25.7 | 974.25 ± 0.62 | 949.17 - 997.98 | 986.88 |
| 61781THB2 | single underlying, upside leverage 2.03x, barrier 100% at maturity | muse-wonder-1.0 | 1 | filing_examples | synthesis_failed | 0.1048 | 22.2 |  |  | 981.90 |
| 46661QNV3 | single underlying, barrier 70% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1144 | 31.9 | 987.04 ± 0.82 | 954.92 - 1011.86 | 986.00 |
| 46661QN84 | single underlying, upside leverage 1x, cap 10%, buffer 80% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.0950 | 19.6 | 991.82 ± 0.60 | 969.22 - 1010.37 | 989.30 |

## What the battery caught

| Check | Failed attempts | Repaired notes |
| --- | --- | --- |
| filing_examples | 2 | 1 |

## Generalisation to template-covered notes

For notes a template can also price, the AI-written payoff is compared with the template on the same stated market inputs.  The difference is shown with the combined standard error of the two Monte Carlo estimates; agreement within 3 combined standard errors is the stated tolerance (3 standard errors).

| CUSIP | Passed | Diff ($) | Combined SE ($) | Within 3 SE | AI examples passed | Template examples passed | Template examples max error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 40058LYL8 | yes | 0.00 | 0.71 | yes | yes | yes | 0.0000 |
| 46661QNW1 | yes | 0.00 | 0.40 | yes | yes | yes | 0.0010 |

## Worked example: a caught error and its repair

Note 06376MPD0 (single underlying, buffer 85% at maturity) passed after 2 attempts.  The first attempt's code was:

```python
import numpy as np

def cashflows(perf, terms):
    perf = np.asarray(perf, dtype=float)
    n_paths = perf.shape[0]
    n_dates = perf.shape[1]
    cash = np.zeros((n_paths, n_dates), dtype=float)
    notional = float(terms["notional"])
    final_index = int(terms["final_index"])
    if perf.shape[2] == 1:
        final_perf = perf[:, final_index, 0]
    else:
        final_perf = np.min(perf[:, final_index, :], axis=1)
    downside = terms["downside"]
    level = float(downside["level"])
    gearing = float(downside["gearing"])
    threshold_settlement = notional * 1.226
    downside_amount = notional * (1.0 + gearing * (final_perf - level))
    downside_amount = np.maximum(downside_amount, 0.0)
    maturity_amount = np.where(final_perf >= level, threshold_settlement, downside_amount)
    cash[:, final_index] = maturity_amount
    return cash
```

Failed checks on attempt 1: filing_examples.

- filing_examples: 20 verified filing examples; cash at the final column must equal the cited payment within $0.01 and other columns must be 0

Counterexample fed back to the model:

~~~text
final/initial 0.5000: the filing pays 588.24, your code paid 588.23 at maturity (max other column 0.00)
final/initial 0.2500: the filing pays 294.12, your code paid 294.10 at maturity (max other column 0.00)
~~~

Feedback sent with the repair request (after the previous code):

~~~text
Failed checks to fix:
- filing_examples: 20 verified filing examples; cash at the final column must equal the cited payment within $0.01 and other columns must be 0
  Counterexample: final/initial 0.5000: the filing pays 588.24, your code paid 588.23 at maturity (max other column 0.00)
final/initial 0.2500: the filing pays 294.12, your code paid 294.10 at maturity (max other column 0.00)
~~~

What the repair changed:

```diff
--- attempt 1
+++ repaired
@@ -15,3 +15,3 @@
     level = float(downside["level"])
-    gearing = float(downside["gearing"])
+    gearing = 1.0 / level
     threshold_settlement = notional * 1.226
```

The repaired code that passed the full battery was:

```python
import numpy as np

def cashflows(perf, terms):
    perf = np.asarray(perf, dtype=float)
    n_paths = perf.shape[0]
    n_dates = perf.shape[1]
    cash = np.zeros((n_paths, n_dates), dtype=float)
    notional = float(terms["notional"])
    final_index = int(terms["final_index"])
    if perf.shape[2] == 1:
        final_perf = perf[:, final_index, 0]
    else:
        final_perf = np.min(perf[:, final_index, :], axis=1)
    downside = terms["downside"]
    level = float(downside["level"])
    gearing = 1.0 / level
    threshold_settlement = notional * 1.226
    downside_amount = notional * (1.0 + gearing * (final_perf - level))
    downside_amount = np.maximum(downside_amount, 0.0)
    maturity_amount = np.where(final_perf >= level, threshold_settlement, downside_amount)
    cash[:, final_index] = maturity_amount
    return cash
```

## Limitations

- Every number here is computed only from the saved benchmark and audit rows; no number is restated from memory or from a model reply.
- Inputs are stated assumptions as of each filing's pricing date, not live market data; preliminary terms, where a filing labels them preliminary, stay labelled preliminary in the saved rows.
- Synthesized ranges exclude skew and use a flat vol, so they are not directly comparable in width with ranges that include skew points.
- A passed battery shows the code reproduced the filing's stated examples and the stated payoff rules on the tested inputs; it does not remove model risk in the stated assumptions themselves.
- Notes with status "needs_review" were priced and then stopped by the plausibility gate; they are not counted as priced with proven AI-written payoffs.

This is a risk analysis under stated assumptions, not investment advice.
