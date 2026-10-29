# Synthesis benchmark

## Method

Templates are disabled for synthesis: for every note in this benchmark, the selected model writes the full payoff code from the filing text and the extracted terms: --model wonder (the default, Muse Wonder 1.0 via the Meta API) or --model open (Muse Spark Open via Baseten, key from BASETEN_API_KEY, final answer only).  Open-model spend is Baseten credits, billed apart from Meta spend, and each saved payoff records which model wrote it.  The generated code is untrusted: it runs only in a subprocess sandbox with a timeout, no network, and an import allow-list (numpy, scipy, math).

The proof battery checks are: called_paths_match_template, convergence, determinism, filing_examples, output_sanity, static_safety.

A failed attempt gets up to 3 repairs.  Each repair is fed the failing check name and a concrete counterexample produced by the battery, so the model sees the exact input on which its code disagreed with the filing or the stated payoff rules.

Pricing uses stated assumptions, not market data as of each pricing date.  Volatility is a flat vol under those stated assumptions and is labelled as such; no volatility skew is fitted.  Synthesized ranges exclude skew (the saved fair_value records this as "skew": "not included"), so synthesized ranges are narrower than template ranges on that point alone.  Fair value is reported as a range under the stated assumptions.  The bank's estimated value is a comparison point only and is never a calibration target.

## Coverage

Templates priced 11 of 42 extracted notes; with proven AI-written payoffs, 26 of 42 (29 of the 29 unsupported notes were attempted).

## Results

- Notes attempted: 31
- Notes passed: 24
- Pass rate: 77.4%
- Passed on the first attempt: 19
- Repaired after a failed attempt: 5
- Failed: 6
- Skipped (fewer than 3 filing examples): 1
- Mean attempts per note: 1.27
- Total model cost (USD): 3.5490
- Cost (meta-models/muse-spark-open-1026, USD): 2.2196
- Cost (muse-wonder-1.0, USD): 1.3293
- 'open' costs are Baseten credits, billed separately from Meta credits.
- Total model seconds: 1476.3
- Status counts: needs_review: 7, priced_ai: 17, skipped_few_examples: 1, synthesis_failed: 6

## Attempts per note

| CUSIP | Structure | Model | Attempts | First failing checks | Status | Cost ($) | Time (s) | Base value ± SE ($) | Range ($) | Bank estimate ($) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 17334DTT9 | single underlying, upside leverage 2.5x, cap 100%, barrier 100% at maturity | meta-models/muse-spark-open-1026 | 1 | filing_examples | synthesis_failed | 0.0628 | 29.5 |  |  | 969.70 |
| 89115NMK7 | single underlying, buffer 87.5% at maturity | meta-models/muse-spark-open-1026 | 2 | filing_examples | priced_ai | 0.1640 | 79.9 | 962.84 ± 1.03 | 915.88 - 1010.63 | 995.20 |
| 89115NKY9 | single underlying, upside leverage 1.25x, cap 28.65%, buffer 70% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1790 | 30.0 | 991.81 ± 0.80 | 954.55 - 1022.16 | 983.60 |
| 40058MC55 | single underlying, upside leverage 1.75x, buffer 80% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.1214 | 69.2 | 1183.62 ± 4.08 | 1103.01 - 1270.27 | 998.00 |
| 40058LYL8 | single underlying, upside leverage 3x, cap 21.2%, barrier 100% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1211 | 25.1 | 942.12 ± 0.50 | 911.60 - 973.57 | 969.00 |
| 40058MG51 | single underlying, upside leverage 2.5x, cap 110%, barrier 100% at maturity | muse-wonder-1.0 | 1 |  | needs_review | 0.1381 | 22.4 | 1053.43 ± 0.48 | 1011.42 - 1094.43 | 962.00 |
| 40058MD88 | single underlying, upside leverage 1x, barrier 100% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.0677 | 28.7 | 1059.75 ± 2.68 | 996.40 - 1127.72 | 961.00 |
| 38151R602 | single underlying, autocall, upside leverage 1.5x, barrier 60% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.1092 | 57.9 | 11.31 ± 0.01 | 10.94 - 11.57 | 9.69 |
| 46661QNW1 | single underlying, upside leverage 1x, cap 14.6%, buffer 90% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1374 | 36.9 | 1000.72 ± 0.28 | 987.52 - 1011.37 | 995.30 |
| 06376MPD0 | single underlying, buffer 85% at maturity | muse-wonder-1.0 | 2 | filing_examples | priced_ai | 0.1737 | 30.0 | 968.96 ± 1.06 | 920.99 - 1018.28 | 997.53 |
| 61781L3Q1 | single underlying, upside leverage 2.3x, barrier 100% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.0878 | 56.8 | 1233.02 ± 4.62 | 1144.62 - 1328.28 | 948.90 |
| 61781LSA9 | single underlying, upside leverage 1.5x, cap 22.2%, buffer 90% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1288 | 28.8 | 964.45 ± 0.44 | 935.81 - 991.70 | 972.80 |
| 61781LRR3 | single underlying, upside leverage 1x, barrier 75% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.0381 | 15.5 | 1047.74 ± 1.89 | 992.33 - 1111.73 | 957.50 |
| 06419TE79 | single underlying, buffer 85% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1372 | 25.7 | 974.25 ± 0.62 | 949.17 - 997.98 | 986.88 |
| 61781LV72 | single underlying, upside leverage 2.09x, barrier 100% at maturity | meta-models/muse-spark-open-1026 | 0 |  | skipped_few_examples | 0.0161 | 4.7 |  |  | 984.00 |
| 90266F6G6 | single underlying, buffer 85% at maturity | meta-models/muse-spark-open-1026 | 2 | filing_examples | priced_ai | 0.1711 | 95.3 | 960.50 ± 0.95 | 917.88 - 1003.22 | 996.20 |
| 90266F6R2 | worst-of 5, upside leverage 1x, buffer 90% at maturity | meta-models/muse-spark-open-1026 | 4 | filing_examples | synthesis_failed | 0.4698 | 304.8 |  |  | 996.00 |
| 06419TL48 | single underlying, buffer 85% at maturity | meta-models/muse-spark-open-1026 | 2 | filing_examples | priced_ai | 0.0964 | 40.2 | 975.37 ± 0.62 | 950.20 - 999.22 | 987.59 |
| 06419TK64 | single underlying, upside leverage 1x, cap 20.1%, barrier 75% at maturity | meta-models/muse-spark-open-1026 | 1 |  | priced_ai | 0.1059 | 52.8 | 948.74 ± 1.13 | 903.92 - 989.68 | 967.25 |
| 95001HTH4 | single underlying, buffer 85% at maturity | meta-models/muse-spark-open-1026 | 2 | filing_examples | priced_ai | 0.0949 | 49.1 | 966.92 ± 1.02 | 921.10 - 1013.83 | 994.42 |
| 95001HTK7 | single underlying, upside leverage 1.5x, cap 16.15%, barrier 100% at maturity | meta-models/muse-spark-open-1026 | 1 |  | priced_ai | 0.0591 | 23.4 | 981.62 ± 0.20 | 973.40 - 990.73 | 988.71 |
| 61781THB2 | single underlying, upside leverage 2.03x, barrier 100% at maturity | muse-wonder-1.0 | 1 | filing_examples | synthesis_failed | 0.1048 | 22.2 |  |  | 981.90 |
| 17334DCA8 | single underlying, upside leverage 1x, cap 13.25%, buffer 90% at maturity | meta-models/muse-spark-open-1026 | 1 | filing_examples | synthesis_failed | 0.0748 | 39.3 |  |  | 968.70 |
| 17334DBW1 | single underlying, barrier 60% at maturity | meta-models/muse-spark-open-1026 | 1 | filing_examples | synthesis_failed | 0.0579 | 26.2 |  |  | 971.60 |
| 17334CU51 | single underlying, upside leverage 1x, cap 17.5%, buffer 100% at maturity | meta-models/muse-spark-open-1026 | 1 | filing_examples | synthesis_failed | 0.0453 | 17.6 |  |  | 950.60 |
| 46661PHE0 | single underlying, buffer 90% at maturity | meta-models/muse-spark-open-1026 | 1 |  | priced_ai | 0.1025 | 58.4 | 962.85 ± 0.68 | 935.82 - 989.54 | 986.70 |
| 46661QLX1 | single underlying, buffer 90% at maturity | meta-models/muse-spark-open-1026 | 1 |  | priced_ai | 0.0959 | 52.4 | 963.29 ± 0.67 | 936.46 - 989.83 | 988.30 |
| 46661QNV3 | single underlying, barrier 70% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.1144 | 31.9 | 987.04 ± 0.82 | 954.92 - 1011.86 | 986.00 |
| 09712D317 | single underlying, upside leverage 1x, cap 40.05%, buffer 75% at maturity | meta-models/muse-spark-open-1026 | 1 |  | needs_review | 0.0870 | 49.2 | 10.39 ± 0.01 | 10.12 - 10.59 | 9.79 |
| 46661QN84 | single underlying, upside leverage 1x, cap 10%, buffer 80% at maturity | muse-wonder-1.0 | 1 |  | priced_ai | 0.0950 | 19.6 | 991.82 ± 0.60 | 969.22 - 1010.37 | 989.30 |
| 46661QQZ1 | single underlying, barrier 90% at maturity | meta-models/muse-spark-open-1026 | 1 |  | priced_ai | 0.0919 | 52.3 | 898.13 ± 1.37 | 772.55 - 1056.64 | 943.30 |

## What the battery caught

| Check | Failed attempts | Repaired notes |
| --- | --- | --- |
| filing_examples | 13 | 5 |

## Generalisation to template-covered notes

For notes a template can also price, the AI-written payoff is compared with the template on the same stated market inputs.  The difference is shown with the combined standard error of the two Monte Carlo estimates; agreement within 3 combined standard errors is the stated tolerance (3 standard errors).

| CUSIP | Passed | Diff ($) | Combined SE ($) | Within 3 SE | AI examples passed | Template examples passed | Template examples max error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 40058LYL8 | yes | 0.00 | 0.71 | yes | yes | yes | 0.0000 |
| 46661QNW1 | yes | 0.00 | 0.40 | yes | yes | yes | 0.0010 |

## Worked example: a caught error and its repair

Note 89115NMK7 (single underlying, buffer 87.5% at maturity) passed after 2 attempts.  The first attempt's code was:

```python
import numpy as np

def cashflows(perf, terms):
    perf = np.asarray(perf, dtype=float)
    n_paths, n_dates, n_assets = perf.shape
    cash = np.zeros((n_paths, n_dates), dtype=float)
    notional = float(terms.get("notional", 1000.0))
    final_idx = int(terms.get("final_index", n_dates - 1))
    autocall = terms.get("autocall")
    coupon = terms.get("coupon")
    downside = terms.get("downside") or {}
    d_level = float(downside.get("level", 0.875)) if downside else 0.875
    d_gearing = float(downside.get("gearing", 1.1429)) if downside else 1.0
    if n_assets > 1:
        worst = np.min(perf, axis=2)
    else:
        worst = perf[:, :, 0]
    if autocall is not None:
        ac_trigger = float(autocall.get("trigger", 1.0))
        ac_amounts = autocall.get("call_amounts", [None] * n_dates)
    else:
        ac_trigger = None
        ac_amounts = [None] * n_dates
    if coupon is not None:
        cp_barrier = float(coupon.get("barrier", 0.0))
        cp_amounts = coupon.get("amounts", [None] * n_dates)
        cp_memory = bool(coupon.get("memory", False))
    else:
        cp_barrier = None
        cp_amounts = [None] * n_dates
        cp_memory = False
    active = np.ones(n_paths, dtype=bool)
    accrued = np.zeros(n_paths, dtype=float)
    unsupported = terms.get("unsupported_features", []) or []
    has_threshold = any("Threshold Settlement" in s for s in unsupported)
    threshold_settlement = 1223.0 * (notional / 1000.0)
    for k in range(n_dates):
        is_final = (k == final_idx)
        if coupon is not None and cp_amounts[k] is not None:
            amt = float(cp_amounts[k])
            if cp_memory:
                eligible = active & (worst[:, k] >= cp_barrier)
                not_eligible = active & (worst[:, k] < cp_barrier)
                if np.any(eligible):
                    pay_amt = amt + accrued[eligible]
                    cash[eligible, k] += pay_amt
                    accrued[eligible] = 0.0
                if np.any(not_eligible):
                    accrued[not_eligible] += amt
            else:
                eligible = active & (worst[:, k] >= cp_barrier)
                if np.any(eligible):
                    cash[eligible, k] += amt
        if autocall is not None and ac_amounts[k] is not None:
            ca = float(ac_amounts[k])
            triggered = active & (worst[:, k] >= ac_trigger)
            if np.any(triggered):
                cash[triggered, k] += ca
                active[triggered] = False
                accrued[triggered] = 0.0
        if is_final:
            still = active
            if np.any(still):
                fp = worst[still, k]
                if has_threshold:
                    buf = notional + notional * d_gearing * (fp - d_level)
                    buf = np.maximum(buf, 0.0)
                    pay = np.where(fp >= d_level, threshold_settlement, buf)
                else:
                    if downside and downside.get("kind") == "buffer":
                        buf = notional + notional * d_gearing * (fp - d_level)
                        buf = np.maximum(buf, 0.0)
                        pay = np.where(fp >= d_level, notional, buf)
                    elif downside and downside.get("kind") == "barrier":
                        breached_pay = notional * (1.0 + d_gearing * (fp - 1.0))
                        breached_pay = np.maximum(breached_pay, 0.0)
                        pay = np.where(fp >= d_level, notional, breached_pay)
                    else:
                        pay = np.full_like(fp, notional, dtype=float)
                    upside = terms.get("upside")
                    if upside is not None:
                        lev = float(upside.get("leverage", 1.0))
                        cap = upside.get("cap")
                        up_pay = notional * (1.0 + lev * (fp - 1.0))
                        if cap is not None:
                            up_pay = np.minimum(up_pay, notional * (1.0 + float(cap)))
                        # apply only where fp > 1 and not in downside loss region
                        # for buffer notes loss region is fp < d_level, for barrier similar
                        # keep simple: upside overrides where fp > 1
                        pay = np.where(fp > 1.0, np.maximum(pay, up_pay), pay)
                cash[still, k] += pay
    return cash
```

Failed checks on attempt 1: filing_examples.

- filing_examples: 17 verified filing examples; cash at the final column must equal the cited payment within $0.01 and other columns must be 0

Counterexample fed back to the model:

~~~text
final/initial 0.5000: the filing pays 571.43, your code paid 571.41 at maturity (max other column 0.00)
final/initial 0.2500: the filing pays 285.71, your code paid 285.69 at maturity (max other column 0.00)
~~~

Feedback sent with the repair request (after the previous code):

~~~text
Failed checks to fix:
- filing_examples: 17 verified filing examples; cash at the final column must equal the cited payment within $0.01 and other columns must be 0
  Counterexample: final/initial 0.5000: the filing pays 571.43, your code paid 571.41 at maturity (max other column 0.00)
final/initial 0.2500: the filing pays 285.71, your code paid 285.69 at maturity (max other column 0.00)
~~~

What the repair changed:

```diff
--- attempt 1
+++ repaired
@@ -6,4 +6,6 @@
     cash = np.zeros((n_paths, n_dates), dtype=float)
+
     notional = float(terms.get("notional", 1000.0))
     final_idx = int(terms.get("final_index", n_dates - 1))
+
     autocall = terms.get("autocall")
@@ -11,4 +13,32 @@
     downside = terms.get("downside") or {}
-    d_level = float(downside.get("level", 0.875)) if downside else 0.875
-    d_gearing = float(downside.get("gearing", 1.1429)) if downside else 1.0
+
+    if downside:
+        d_level = float(downside.get("level", 0.875))
+        d_gearing_raw = float(downside.get("gearing", 1.0))
+        d_kind = downside.get("kind", "buffer")
+    else:
+        d_level = 0.875
+        d_gearing_raw = 1.0
+        d_kind = "buffer"
+
+    # detect digital threshold settlement feature
+    unsupported = terms.get("unsupported_features", []) or []
+    has_threshold = any("Threshold Settlement" in s for s in unsupported)
+
+    # For the Digital note the downside multiplier is exactly Initial/Threshold = 1/level.
+    # The term-sheet rounds it to 1.1429, we must use the exact value to match filing examples.
+    if has_threshold and d_kind == "buffer" and d_level != 0:
+        d_gearing = 1.0 / d_level
+    else:
+        # for ordinary buffer notes use exact 1/level if the given gearing is just a rounded
+        # version of 1/level (protects against rounding to 4 decimals)
+        if d_kind == "buffer" and d_level != 0:
+            implied = 1.0 / d_level
+            if abs(d_gearing_raw - implied) < 1e-3:
+                d_gearing = implied
+            else:
+                d_gearing = d_gearing_raw
+        else:
+            d_gearing = d_gearing_raw
+
     if n_assets > 1:
@@ -17,2 +47,3 @@
         worst = perf[:, :, 0]
+
     if autocall is not None:
@@ -23,2 +54,3 @@
         ac_amounts = [None] * n_dates
+
     if coupon is not None:
@@ -31,9 +63,12 @@
         cp_memory = False
+
     active = np.ones(n_paths, dtype=bool)
     accrued = np.zeros(n_paths, dtype=float)
-    unsupported = terms.get("unsupported_features", []) or []
-    has_threshold = any("Threshold Settlement" in s for s in unsupported)
+
+    # $1,223 per $1,000 – scaled with notional
     threshold_settlement = 1223.0 * (notional / 1000.0)
+
     for k in range(n_dates):
         is_final = (k == final_idx)
+
         if coupon is not None and cp_amounts[k] is not None:
@@ -53,2 +88,3 @@
                     cash[eligible, k] += amt
+
         if autocall is not None and ac_amounts[k] is not None:
@@ -60,2 +96,3 @@
                 accrued[triggered] = 0.0
+
         if is_final:
@@ -86,7 +123,5 @@
                             up_pay = np.minimum(up_pay, notional * (1.0 + float(cap)))
-                        # apply only where fp > 1 and not in downside loss region
-                        # for buffer notes loss region is fp < d_level, for barrier similar
-                        # keep simple: upside overrides where fp > 1
                         pay = np.where(fp > 1.0, np.maximum(pay, up_pay), pay)
                 cash[still, k] += pay
+
     return cash
```

The repaired code that passed the full battery was:

```python
import numpy as np

def cashflows(perf, terms):
    perf = np.asarray(perf, dtype=float)
    n_paths, n_dates, n_assets = perf.shape
    cash = np.zeros((n_paths, n_dates), dtype=float)

    notional = float(terms.get("notional", 1000.0))
    final_idx = int(terms.get("final_index", n_dates - 1))

    autocall = terms.get("autocall")
    coupon = terms.get("coupon")
    downside = terms.get("downside") or {}

    if downside:
        d_level = float(downside.get("level", 0.875))
        d_gearing_raw = float(downside.get("gearing", 1.0))
        d_kind = downside.get("kind", "buffer")
    else:
        d_level = 0.875
        d_gearing_raw = 1.0
        d_kind = "buffer"

    # detect digital threshold settlement feature
    unsupported = terms.get("unsupported_features", []) or []
    has_threshold = any("Threshold Settlement" in s for s in unsupported)

    # For the Digital note the downside multiplier is exactly Initial/Threshold = 1/level.
    # The term-sheet rounds it to 1.1429, we must use the exact value to match filing examples.
    if has_threshold and d_kind == "buffer" and d_level != 0:
        d_gearing = 1.0 / d_level
    else:
        # for ordinary buffer notes use exact 1/level if the given gearing is just a rounded
        # version of 1/level (protects against rounding to 4 decimals)
        if d_kind == "buffer" and d_level != 0:
            implied = 1.0 / d_level
            if abs(d_gearing_raw - implied) < 1e-3:
                d_gearing = implied
            else:
                d_gearing = d_gearing_raw
        else:
            d_gearing = d_gearing_raw

    if n_assets > 1:
        worst = np.min(perf, axis=2)
    else:
        worst = perf[:, :, 0]

    if autocall is not None:
        ac_trigger = float(autocall.get("trigger", 1.0))
        ac_amounts = autocall.get("call_amounts", [None] * n_dates)
    else:
        ac_trigger = None
        ac_amounts = [None] * n_dates

    if coupon is not None:
        cp_barrier = float(coupon.get("barrier", 0.0))
        cp_amounts = coupon.get("amounts", [None] * n_dates)
        cp_memory = bool(coupon.get("memory", False))
    else:
        cp_barrier = None
        cp_amounts = [None] * n_dates
        cp_memory = False

    active = np.ones(n_paths, dtype=bool)
    accrued = np.zeros(n_paths, dtype=float)

    # $1,223 per $1,000 – scaled with notional
    threshold_settlement = 1223.0 * (notional / 1000.0)

    for k in range(n_dates):
        is_final = (k == final_idx)

        if coupon is not None and cp_amounts[k] is not None:
            amt = float(cp_amounts[k])
            if cp_memory:
                eligible = active & (worst[:, k] >= cp_barrier)
                not_eligible = active & (worst[:, k] < cp_barrier)
                if np.any(eligible):
                    pay_amt = amt + accrued[eligible]
                    cash[eligible, k] += pay_amt
                    accrued[eligible] = 0.0
                if np.any(not_eligible):
                    accrued[not_eligible] += amt
            else:
                eligible = active & (worst[:, k] >= cp_barrier)
                if np.any(eligible):
                    cash[eligible, k] += amt

        if autocall is not None and ac_amounts[k] is not None:
            ca = float(ac_amounts[k])
            triggered = active & (worst[:, k] >= ac_trigger)
            if np.any(triggered):
                cash[triggered, k] += ca
                active[triggered] = False
                accrued[triggered] = 0.0

        if is_final:
            still = active
            if np.any(still):
                fp = worst[still, k]
                if has_threshold:
                    buf = notional + notional * d_gearing * (fp - d_level)
                    buf = np.maximum(buf, 0.0)
                    pay = np.where(fp >= d_level, threshold_settlement, buf)
                else:
                    if downside and downside.get("kind") == "buffer":
                        buf = notional + notional * d_gearing * (fp - d_level)
                        buf = np.maximum(buf, 0.0)
                        pay = np.where(fp >= d_level, notional, buf)
                    elif downside and downside.get("kind") == "barrier":
                        breached_pay = notional * (1.0 + d_gearing * (fp - 1.0))
                        breached_pay = np.maximum(breached_pay, 0.0)
                        pay = np.where(fp >= d_level, notional, breached_pay)
                    else:
                        pay = np.full_like(fp, notional, dtype=float)
                    upside = terms.get("upside")
                    if upside is not None:
                        lev = float(upside.get("leverage", 1.0))
                        cap = upside.get("cap")
                        up_pay = notional * (1.0 + lev * (fp - 1.0))
                        if cap is not None:
                            up_pay = np.minimum(up_pay, notional * (1.0 + float(cap)))
                        pay = np.where(fp > 1.0, np.maximum(pay, up_pay), pay)
                cash[still, k] += pay

    return cash
```

## Limitations

- Every number here is computed only from the saved benchmark and audit rows; no number is restated from memory or from a model reply.
- Inputs are stated assumptions as of each filing's pricing date, not live market data; preliminary terms, where a filing labels them preliminary, stay labelled preliminary in the saved rows.
- Synthesized ranges exclude skew and use a flat vol, so they are not directly comparable in width with ranges that include skew points.
- A passed battery shows the code reproduced the filing's stated examples and the stated payoff rules on the tested inputs; it does not remove model risk in the stated assumptions themselves.
- Notes with status "needs_review" were priced and then stopped by the plausibility gate; they are not counted as priced with proven AI-written payoffs.

This is a risk analysis under stated assumptions, not investment advice.
