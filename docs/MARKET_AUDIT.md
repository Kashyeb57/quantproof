# Market audit

Each row is one saved audit result. Our estimated fair value is shown as a range, and the bank's own estimated value is shown alongside it for every note.

## Aggregate

- Notes read: 44
- Notes extracted: 42
- Notes priced: 12
- Needs review (plausibility gate; not counted as priced): 1
- Share priced: 28.6%
- Notes with a bank estimated value: 42
- Median cost to investor (per $1,000 notional, bank's figure): 19.15
- Median cost to investor (%, bank's figure): 1.9%
- Bank estimated value within our range (priced and needs-review notes): 12 of 13 (92.3%)
- Quote verification rate: 95.2%
- Total model cost (USD): 5.4949
- Total model seconds: 2817.6
- Issuers: Bank of America, Bank of Montreal, Bank of Nova Scotia, Citigroup, Goldman Sachs, JPMorgan, Morgan Stanley, Royal Bank of Canada, Toronto-Dominion, UBS, Wells Fargo
- Status counts: extraction_failed: 2, needs_review: 1, priced: 12, unsupported: 29
- Skipped: 190

## Per-note results

| Issuer | CUSIP | Underlyings | Structure | Issue price ($) | Bank's estimated value ($) | Cost to investor ($, bank's figure) | Our range low ($) | Our range high ($) | Real-world P(loss) | Inputs | Quotes verified | Status | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bank of America | 09712D598 | The TJX Companies, Inc. | single underlying, autocall, upside leverage 1.6x, barrier 100% at maturity | 10.00 | 9.56 | 0.44 | 9.28 | 9.91 | 23.4% | r 0.0472 (U.S. Treasury par yield, 2026-10-22, 3.01y interpolated between 3 Yr and 5 Yr); vol [0.25]; rho 0.00 (single underlying: rho 0.0) | 96.0% | priced |  |
| Bank of America | 09712D887 | First Trust Nasdaq Cybersecurity ETF | single underlying, autocall, barrier 100% at maturity | 10.00 | 9.51 | 0.49 | 9.22 | 10.03 | 20.9% | r 0.0472 (U.S. Treasury par yield, 2026-10-22, 3.01y interpolated between 3 Yr and 5 Yr); vol [0.25]; rho 0.00 (single underlying: rho 0.0) | 96.7% | priced |  |
| Bank of America | 09712D317 | iShares Semiconductor ETF | single underlying, upside leverage 1x, cap 40.05%, buffer 75% at maturity | 10.00 | 9.79 | 0.21 |  |  |  |  | 95.7% | unsupported | unsupported features: ['Absolute Return Buffer'] |
| Bank of Montreal | 06368N326 | Russell 2000® Index | single underlying, autocall, upside leverage 2.11x, barrier 100% at maturity | 10.00 | 9.59 | 0.41 | 9.31 | 9.88 | 18.4% | r 0.0485 (U.S. Treasury par yield, 2026-10-15, 3.03y interpolated between 3 Yr and 5 Yr); vol [0.21]; rho 0.00 (single underlying: rho 0.0) | 96.2% | priced |  |
| Bank of Montreal | 06376MPD0 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 997.53 | 2.47 |  |  |  |  | 100.0% | unsupported | unsupported features: ['threshold settlement amount'] |
| Bank of Montreal | 06376MPC2 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 25.9%, buffer 87.5% at maturity | 1000.00 | 996.26 | 3.74 | 942.31 | 1010.09 | 11.3% | r 0.0476 (U.S. Treasury par yield, 2026-10-21, 1.82y interpolated between 1 Yr and 2 Yr); vol [0.17]; rho 0.00 (single underlying: rho 0.0) | 95.0% | priced |  |
| Bank of Nova Scotia | 06419TE79 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 986.88 | 13.12 |  |  |  |  | 95.0% | unsupported | unsupported features: ['Digital Return'] |
| Bank of Nova Scotia | 06419TL48 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 987.59 | 12.41 |  |  |  |  | 95.0% | unsupported | unsupported features: ['Digital Return'] |
| Bank of Nova Scotia | 06419TK64 | S&P 500® Index | single underlying, upside leverage 1x, cap 20.1%, barrier 75% at maturity | 1000.00 | 967.25 | 32.75 |  |  |  |  | 95.5% | unsupported | unsupported features: ['Absolute Reference Asset Return'] |
| Citigroup | 17334DTT9 | S&P 500® Index | single underlying, upside leverage 2.5x, cap 100%, barrier 100% at maturity | 1000.00 | 969.70 | 30.30 |  |  |  |  | 90.9% | unsupported | unsupported features: ['modified inverse exposure'] |
| Citigroup | 17334DCA8 | S&P 500® Index | single underlying, upside leverage 1x, cap 13.25%, buffer 90% at maturity | 1000.00 | 968.70 | 31.30 |  |  |  |  | 95.5% | unsupported | unsupported features: ['Absolute return amount'] |
| Citigroup | 17334DBW1 | Boston Scientific Corporation | single underlying, barrier 60% at maturity | 1000.00 | 971.60 | 28.40 |  |  |  |  | 95.7% | unsupported | unsupported features: ['digital return'] |
| Citigroup | 17334CUE2 | S&P 500® Index | single underlying, upside leverage 1x, cap 11.15%, barrier 80% at maturity | 1000.00 | 977.60 | 22.40 | 942.43 | 996.49 | 2.2% | r 0.0451 (U.S. Treasury par yield, 2026-09-25, 1.02y interpolated between 1 Yr and 2 Yr); vol [0.15]; rho 0.00 (single underlying: rho 0.0) | 95.2% | priced |  |
| Citigroup | 17334CU51 | Dow Jones Industrial AverageTM | single underlying, upside leverage 1x, cap 17.5%, buffer 100% at maturity | 1000.00 | 950.60 | 49.40 |  |  |  |  | 87.5% | unsupported | unsupported features: ['principal protection (no loss of principal at maturity)'] |
| Citigroup | 17334DA77 | MSCI ACWI Index | single underlying, upside leverage 1x, cap 12.85%, barrier 100% at maturity | 1000.00 | 966.30 | 33.70 | 813.64 | 886.08 | 37.3% | r 0.0482 (U.S. Treasury par yield, 2026-10-14, 2.02y interpolated between 2 Yr and 3 Yr); vol [0.25]; rho 0.00 (single underlying: rho 0.0) | 90.9% | needs_review | plausibility gate: bank's estimated value 966.30 is above our whole range (high 886.08) |
| Goldman Sachs | 40058MC55 | S&P 500® Index | single underlying, upside leverage 1.75x, buffer 80% at maturity | 1000.00 | 998.00 | 2.00 |  |  |  |  | 95.2% | unsupported | unsupported features: ['Company’s redemption right'] |
| Goldman Sachs | 40058LYL8 | Russell 2000® Index | single underlying, upside leverage 3x, cap 21.2%, barrier 100% at maturity | 1000.00 | 969.00 | 31.00 | 916.55 | 979.45 | 38.7% | r 0.0454 (U.S. Treasury par yield, 2026-10-16, 1.30y interpolated between 1 Yr and 2 Yr); vol [0.23]; rho 0.00 (single underlying: rho 0.0) | 95.5% | priced |  |
| Goldman Sachs | 38151R701 | the common stock of Constellation Energy Corporation; the common stock of Quanta Services, Inc.; the common stock of Vistra Corp. | worst-of 3, autocall, contingent coupon (memory), barrier 80% at maturity | 10.00 | 9.48 | 0.52 | 8.10 | 10.27 | 20.2% | r 0.0482 (U.S. Treasury par yield, 2026-10-16, 3.02y interpolated between 3 Yr and 5 Yr); vol [0.25; 0.25; 0.25]; rho 0.60 (no pair with two confirmed FRED closes series: stated 0.6) | 98.7% | priced |  |
| Goldman Sachs | 40058MG51 | S&P 500® Index | single underlying, upside leverage 2.5x, cap 110%, barrier 100% at maturity | 1000.00 | 962.00 | 38.00 |  |  |  |  | 82.6% | unsupported | unsupported features: ['bearish view'] |
| Goldman Sachs |  |  |  |  |  |  |  |  |  |  |  | extraction_failed | reply is not valid JSON: Expecting ',' delimiter: line 1 column 7687 (char 7686) |
| Goldman Sachs | 40058MD88 | S&P 500® Index | single underlying, upside leverage 1x, barrier 100% at maturity | 1000.00 | 961.00 | 39.00 |  |  |  |  | 95.2% | unsupported | unsupported features: ['Company’s redemption right'] |
| Goldman Sachs | 38151R602 | The Class A common stock of Palantir Technologies Inc. | single underlying, autocall, upside leverage 1.5x, barrier 60% at maturity | 10.00 | 9.69 | 0.31 |  |  |  |  | 96.4% | unsupported | unsupported features: ['absolute value of the percentage decline'] |
| JPMorgan | 46661QNW1 | TOPIX® Index | single underlying, upside leverage 1x, cap 14.6%, buffer 90% at maturity | 1000.00 | 995.30 | 4.70 | 981.39 | 1009.75 | 22.7% | r 0.0435 (U.S. Treasury par yield, 2026-10-21, 0.51y interpolated between 0.5 Yr and 1 Yr); vol [0.25]; rho 0.00 (single underlying: rho 0.0) | 95.8% | priced |  |
| JPMorgan | 46661PHE0 | S&P 500® Index | single underlying, buffer 90% at maturity | 1000.00 | 986.70 | 13.30 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QLX1 | S&P 500® Index | single underlying, buffer 90% at maturity | 1000.00 | 988.30 | 11.70 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QNV3 | S&P 500® Index | single underlying, barrier 70% at maturity | 1000.00 | 986.00 | 14.00 |  |  |  |  | 91.3% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QN84 | S&P 500® Index | single underlying, upside leverage 1x, cap 10%, buffer 80% at maturity | 1000.00 | 989.30 | 10.70 |  |  |  |  | 95.7% | unsupported | unsupported features: ['Absolute Index Return'] |
| JPMorgan | 46661QQZ1 | S&P 500® Index | single underlying, barrier 90% at maturity | 1000.00 | 943.30 | 56.70 |  |  |  |  | 91.3% | unsupported | unsupported features: ['Contingent Digital Return'] |
| Morgan Stanley | 61780K566 | S&P 500® Index | single underlying, upside leverage 3x, cap 13.25%, barrier 100% at maturity | 10.00 | 9.80 | 0.20 | 9.43 | 9.99 | 30.9% | r 0.0446 (U.S. Treasury par yield, 2026-09-21, 1.04y interpolated between 1 Yr and 2 Yr); vol [0.15]; rho 0.00 (single underlying: rho 0.0) | 91.7% | priced |  |
| Morgan Stanley | 61781L3Q1 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.3x, barrier 100% at maturity | 1000.00 | 948.90 | 51.10 |  |  |  |  | 95.7% | unsupported | excess-return index underlying is not modelled by the template (no financing in its drift): S&P 500® Futures Excess Return Index |
| Morgan Stanley | 61781LSA9 | EURO STOXX 50® Index | single underlying, upside leverage 1.5x, cap 22.2%, buffer 90% at maturity | 1000.00 | 972.80 | 27.20 |  |  |  |  | 92.0% | unsupported | unsupported features: ['Minimum payment at maturity'] |
| Morgan Stanley | 61781LRR3 | EURO STOXX 50® Index | single underlying, upside leverage 1x, barrier 75% at maturity | 1000.00 | 957.50 | 42.50 |  |  |  |  | 91.7% | unsupported | unsupported features: ['Upside payment'] |
| Morgan Stanley | 61781LV72 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.09x, barrier 100% at maturity | 1000.00 | 984.00 | 16.00 |  |  |  |  | 95.7% | unsupported | principal protection is not modelled by the template: "Under no circumstances will the payment at maturity be less than the stated principal amount. Upside payment: \| stated principal amount × participation rate × u" |
| Morgan Stanley | 61781THB2 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.03x, barrier 100% at maturity | 1000.00 | 981.90 | 18.10 |  |  |  |  | 95.7% | unsupported | principal protection is not modelled by the template: "Under no circumstances will the payment at maturity be less than the stated principal amount. Upside payment: \| stated principal amount × participation rate × u" |
| Royal Bank of Canada | 78018DG43 | S&P 500® Index | single underlying, upside leverage 1.5x, cap 19.725%, barrier 100% at maturity | 1000.00 | 984.08 | 15.92 | 927.88 | 985.05 | 34.0% | r 0.0457 (U.S. Treasury par yield, 2026-10-19, 1.26y interpolated between 1 Yr and 2 Yr); vol [0.18]; rho 0.00 (single underlying: rho 0.0) | 85.7% | priced |  |
| Toronto-Dominion | 89115NMK7 | S&P 500® Index | single underlying, buffer 87.5% at maturity | 1000.00 | 995.20 | 4.80 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold Settlement Amount'] |
| Toronto-Dominion | 89115NKY9 | S&P 500® Index | single underlying, upside leverage 1.25x, cap 28.65%, buffer 70% at maturity | 1000.00 | 983.60 | 16.40 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Absolute Return'] |
| Toronto-Dominion |  |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.review_dates: expected cited field, got null; autocall.call_amounts: expected cited field, got null |
| UBS | 90266F3B0 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 24.08%, buffer 87.5% at maturity | 1000.00 | 996.70 | 3.30 | 935.57 | 1004.27 | 14.3% | r 0.0470 (U.S. Treasury par yield, 2026-10-19, 1.73y interpolated between 1 Yr and 2 Yr); vol [0.18]; rho 0.00 (single underlying: rho 0.0) | 95.7% | priced |  |
| UBS | 90266F6G6 | MSCI EAFE® Index | single underlying, buffer 85% at maturity | 1000.00 | 996.20 | 3.80 |  |  |  |  | 100.0% | unsupported | unsupported features: ['maximum settlement amount'] |
| UBS | 90266F6R2 | EURO STOXX 50® Index; TOPIX; FTSE® 100 Index; Swiss Market Index; S&P/ASX 200 Index | worst-of 5, upside leverage 1x, buffer 90% at maturity | 1000.00 | 996.00 | 4.00 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold settlement amount'] |
| Wells Fargo | 95001HTE1 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 34.832%, buffer 85% at maturity | 1000.00 | 995.25 | 4.75 | 922.45 | 1008.86 | 12.9% | r 0.0468 (U.S. Treasury par yield, 2026-10-22, 2.38y interpolated between 2 Yr and 3 Yr); vol [0.19]; rho 0.00 (single underlying: rho 0.0) | 95.8% | priced |  |
| Wells Fargo | 95001HTH4 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 994.42 | 5.58 |  |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold Settlement Amount'] |
| Wells Fargo | 95001HTK7 | S&P 500® Index | single underlying, upside leverage 1.5x, cap 16.15%, barrier 100% at maturity | 1000.00 | 988.71 | 11.29 |  |  |  |  | 92.0% | unsupported | unsupported features: ['principal protection (no loss of principal at maturity)'] |

## Assumptions

- correlation: realised
- credit_spread: 0.006
- dividend: 0.01
- erp: 0.05
- note: Inputs are anchored where a licence allows and stated otherwise: risk_free_rate is the U.S. Treasury par yield on each note's pricing date interpolated to its maturity; vol is the 30-day implied-vol index close (VIX/VXN/RVX) on the pricing date as the grid centre for index underlyings (single stocks and others keep stated 0.25); correlation is the average realised pairwise correlation from cached FRED closes where both legs exist, else stated 0.6 (0 for one underlying); credit_spread 0.006, dividend 0.01 and erp 0.05 stay stated. The per-note market_inputs record states every source and date, including anything missing or not anchored.
- risk_free_rate: Treasury
- vol: implied

Volatilities, dividends, correlation, rate and credit spread are anchored where a licence allows and stated otherwise: the risk-free rate is the U.S. Treasury par yield on each note's pricing date interpolated to its maturity; index volatilities are 30-day implied-vol closes as grid centres (a stated simplification); correlation is realised from cached FRED closes where both legs exist, else stated; credit spread, dividends and the ERP stay stated. The Inputs column states every source and date, including anything missing or not anchored.

Cross-check note: each extraction was compared with a second extraction by a different model version, and any differing fields are recorded in the saved results. This is a weaker independence check than two genuinely different model services, and the audit outputs should be read with that limitation stated.

This is a risk analysis under stated assumptions, not investment advice.
