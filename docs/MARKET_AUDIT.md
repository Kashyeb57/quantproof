# Market audit

Each row is one saved audit result. Our estimated fair value is shown as a range, and the bank's own estimated value is shown alongside it for every note.

## Aggregate

- Notes read: 44
- Notes extracted: 34
- Notes priced: 2
- Needs review (plausibility gate; not counted as priced): 6
- Share priced: 5.9%
- Notes with a bank estimated value: 34
- Median cost to investor (per $1,000 notional, bank's figure): 15.96
- Median cost to investor (%, bank's figure): 1.6%
- Bank estimated value within our range (priced notes): 2 of 2 (100.0%)
- Quote verification rate: 95.2%
- Total model cost (USD): 4.8835
- Total model seconds: 2634.2
- Issuers: Bank of America, Bank of Montreal, Bank of Nova Scotia, Citigroup, Goldman Sachs, JPMorgan, Morgan Stanley, Royal Bank of Canada, Toronto-Dominion, UBS, Wells Fargo
- Status counts: extraction_failed: 10, needs_review: 6, priced: 2, unsupported: 26
- Skipped: 190

## Per-note results

| Issuer | CUSIP | Underlyings | Structure | Issue price ($) | Bank's estimated value ($) | Cost to investor ($, bank's figure) | Our range low ($) | Our range high ($) | Real-world P(loss) | Quotes verified | Status | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bank of America |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.payment_dates.value: expected list, got NoneType |
| Bank of America |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.payment_dates.value: expected list, got NoneType |
| Bank of America | 09712D317 | iShares Semiconductor ETF | single underlying, upside leverage 1x, cap 40.05%, buffer 75% at maturity | 10.00 | 9.79 | 0.21 |  |  |  | 95.7% | unsupported | unsupported features: ['Absolute Return Buffer'] |
| Bank of Montreal |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.payment_dates.value: expected list, got NoneType |
| Bank of Montreal | 06376MPD0 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 997.53 | 2.47 |  |  |  | 100.0% | unsupported | unsupported features: ['threshold settlement amount'] |
| Bank of Montreal | 06376MPC2 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 25.9%, buffer 87.5% at maturity | 1000.00 | 996.26 | 3.74 | 908.60 | 988.37 | 25.9% | 95.0% | needs_review | plausibility gate: bank's estimated value 996.26 is above our whole range (high 988.37) |
| Bank of Nova Scotia | 06419TE79 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 986.88 | 13.12 |  |  |  | 95.0% | unsupported | unsupported features: ['Digital Return'] |
| Bank of Nova Scotia | 06419TL48 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 987.59 | 12.41 |  |  |  | 95.0% | unsupported | unsupported features: ['Digital Return'] |
| Bank of Nova Scotia | 06419TK64 | S&P 500® Index | single underlying, upside leverage 1x, cap 20.1%, barrier 75% at maturity | 1000.00 | 967.25 | 32.75 |  |  |  | 95.5% | unsupported | unsupported features: ['Absolute Reference Asset Return'] |
| Citigroup | 17334DTT9 | S&P 500® Index | single underlying, upside leverage 2.5x, cap 100%, barrier 100% at maturity | 1000.00 | 969.70 | 30.30 |  |  |  | 90.9% | unsupported | unsupported features: ['modified inverse exposure'] |
| Citigroup | 17334DCA8 | S&P 500® Index | single underlying, upside leverage 1x, cap 13.25%, buffer 90% at maturity | 1000.00 | 968.70 | 31.30 |  |  |  | 95.5% | unsupported | unsupported features: ['Absolute return amount'] |
| Citigroup | 17334DBW1 | Boston Scientific Corporation | single underlying, barrier 60% at maturity | 1000.00 | 971.60 | 28.40 |  |  |  | 95.7% | unsupported | unsupported features: ['digital return'] |
| Citigroup | 17334CUE2 | S&P 500® Index | single underlying, upside leverage 1x, cap 11.15%, barrier 80% at maturity | 1000.00 | 977.60 | 22.40 | 900.72 | 972.86 | 13.7% | 95.2% | needs_review | plausibility gate: bank's estimated value 977.60 is above our whole range (high 972.86) |
| Citigroup |  |  |  |  |  |  |  |  |  |  | extraction_failed | downside level must be a fraction of initial in (0, 1] |
| Citigroup |  |  |  |  |  |  |  |  |  |  | extraction_failed | downside level must be a fraction of initial in (0, 1] |
| Goldman Sachs | 40058MC55 | S&P 500® Index | single underlying, upside leverage 1.75x, buffer 80% at maturity | 1000.00 | 998.00 | 2.00 |  |  |  | 95.2% | unsupported | unsupported features: ['Company’s redemption right'] |
| Goldman Sachs | 40058LYL8 | Russell 2000® Index | single underlying, upside leverage 3x, cap 21.2%, barrier 100% at maturity | 1000.00 | 969.00 | 31.00 | 911.60 | 973.57 | 42.0% | 95.5% | priced |  |
| Goldman Sachs |  |  |  |  |  |  |  |  |  |  | extraction_failed | reply is not valid JSON: Expecting ',' delimiter: line 1 column 7252 (char 7251) |
| Goldman Sachs | 40058MG51 | S&P 500® Index | single underlying, upside leverage 2.5x, cap 110%, barrier 100% at maturity | 1000.00 | 962.00 | 38.00 |  |  |  | 82.6% | unsupported | unsupported features: ['bearish view'] |
| Goldman Sachs |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.payment_dates.value: expected list, got NoneType; coupon.payment_dates.value: expected list, got NoneType |
| Goldman Sachs | 40058MD88 | S&P 500® Index | single underlying, upside leverage 1x, barrier 100% at maturity | 1000.00 | 961.00 | 39.00 |  |  |  | 95.2% | unsupported | unsupported features: ['Company’s redemption right'] |
| Goldman Sachs |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.payment_dates.value: expected list, got NoneType |
| JPMorgan | 46661QNW1 | TOPIX® Index | single underlying, upside leverage 1x, cap 14.6%, buffer 90% at maturity | 1000.00 | 995.30 | 4.70 | 982.99 | 1011.37 | 23.2% | 95.8% | priced |  |
| JPMorgan | 46661PHE0 | S&P 500® Index | single underlying, buffer 90% at maturity | 1000.00 | 986.70 | 13.30 |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QLX1 | S&P 500® Index | single underlying, buffer 90% at maturity | 1000.00 | 988.30 | 11.70 |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QNV3 | S&P 500® Index | single underlying, barrier 70% at maturity | 1000.00 | 986.00 | 14.00 |  |  |  | 91.3% | unsupported | unsupported features: ['Contingent Digital Return'] |
| JPMorgan | 46661QN84 | S&P 500® Index | single underlying, upside leverage 1x, cap 10%, buffer 80% at maturity | 1000.00 | 989.30 | 10.70 |  |  |  | 95.7% | unsupported | unsupported features: ['Absolute Index Return'] |
| JPMorgan | 46661QQZ1 | S&P 500® Index | single underlying, barrier 90% at maturity | 1000.00 | 943.30 | 56.70 |  |  |  | 91.3% | unsupported | unsupported features: ['Contingent Digital Return'] |
| Morgan Stanley | 61780K566 | S&P 500® Index | single underlying, upside leverage 3x, cap 13.25%, barrier 100% at maturity | 10.00 | 9.80 | 0.20 | 9.04 | 9.56 | 42.8% | 91.7% | needs_review | plausibility gate: bank's estimated value 9.80 is above our whole range (high 9.56) |
| Morgan Stanley | 61781L3Q1 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.3x, barrier 100% at maturity | 1000.00 | 948.90 | 51.10 |  |  |  | 95.7% | unsupported | excess-return index underlying is not modelled by the template (no financing in its drift): S&P 500® Futures Excess Return Index |
| Morgan Stanley | 61781LSA9 | EURO STOXX 50® Index | single underlying, upside leverage 1.5x, cap 22.2%, buffer 90% at maturity | 1000.00 | 972.80 | 27.20 |  |  |  | 92.0% | unsupported | unsupported features: ['Minimum payment at maturity'] |
| Morgan Stanley | 61781LRR3 | EURO STOXX 50® Index | single underlying, upside leverage 1x, barrier 75% at maturity | 1000.00 | 957.50 | 42.50 |  |  |  | 91.7% | unsupported | unsupported features: ['Upside payment'] |
| Morgan Stanley | 61781LV72 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.09x, barrier 100% at maturity | 1000.00 | 984.00 | 16.00 |  |  |  | 95.7% | unsupported | principal protection is not modelled by the template: "Under no circumstances will the payment at maturity be less than the stated principal amount. Upside payment: \| stated principal amount × participation rate × u" |
| Morgan Stanley | 61781THB2 | S&P 500® Futures Excess Return Index | single underlying, upside leverage 2.03x, barrier 100% at maturity | 1000.00 | 981.90 | 18.10 |  |  |  | 95.7% | unsupported | principal protection is not modelled by the template: "Under no circumstances will the payment at maturity be less than the stated principal amount. Upside payment: \| stated principal amount × participation rate × u" |
| Royal Bank of Canada | 78018DG43 | S&P 500® Index | single underlying, upside leverage 1.5x, cap 19.725%, barrier 100% at maturity | 1000.00 | 984.08 | 15.92 | 902.69 | 960.02 | 42.1% | 85.7% | needs_review | plausibility gate: bank's estimated value 984.08 is above our whole range (high 960.02) |
| Toronto-Dominion | 89115NMK7 | S&P 500® Index | single underlying, buffer 87.5% at maturity | 1000.00 | 995.20 | 4.80 |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold Settlement Amount'] |
| Toronto-Dominion | 89115NKY9 | S&P 500® Index | single underlying, upside leverage 1.25x, cap 28.65%, buffer 70% at maturity | 1000.00 | 983.60 | 16.40 |  |  |  | 100.0% | unsupported | unsupported features: ['Contingent Absolute Return'] |
| Toronto-Dominion |  |  |  |  |  |  |  |  |  |  | extraction_failed | autocall.review_dates.value: expected list, got NoneType; autocall.payment_dates.value: expected list, got NoneType; autocall.call_amounts.value: expected list, got NoneType |
| UBS | 90266F3B0 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 24.08%, buffer 87.5% at maturity | 1000.00 | 996.70 | 3.30 | 910.43 | 987.55 | 25.8% | 95.7% | needs_review | plausibility gate: bank's estimated value 996.70 is above our whole range (high 987.55) |
| UBS | 90266F6G6 | MSCI EAFE® Index | single underlying, buffer 85% at maturity | 1000.00 | 996.20 | 3.80 |  |  |  | 100.0% | unsupported | unsupported features: ['maximum settlement amount'] |
| UBS | 90266F6R2 | EURO STOXX 50® Index; TOPIX; FTSE® 100 Index; Swiss Market Index; S&P/ASX 200 Index | worst-of 5, upside leverage 1x, buffer 90% at maturity | 1000.00 | 996.00 | 4.00 |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold settlement amount'] |
| Wells Fargo | 95001HTE1 | S&P 500® Index | single underlying, upside leverage 1.4x, cap 34.832%, buffer 85% at maturity | 1000.00 | 995.25 | 4.75 | 897.01 | 993.24 | 23.8% | 95.8% | needs_review | plausibility gate: bank's estimated value 995.25 is above our whole range (high 993.24) |
| Wells Fargo | 95001HTH4 | S&P 500® Index | single underlying, buffer 85% at maturity | 1000.00 | 994.42 | 5.58 |  |  |  | 100.0% | unsupported | unsupported features: ['Threshold Settlement Amount'] |
| Wells Fargo |  |  |  |  |  |  |  |  |  |  | extraction_failed | downside level must be a fraction of initial in (0, 1] |

## Assumptions

- correlation: 0.6
- credit_spread: 0.006
- dividend: 0.01
- erp: 0.05
- note: These are stated assumptions, not market data as of each pricing date: risk_free_rate 0.037, credit_spread 0.006, vol 0.25 per underlying, dividend 0.01 per underlying, pairwise correlation 0.6 (0 for one underlying), erp 0.05.
- risk_free_rate: 0.037
- vol: 0.25

Volatilities, dividends, correlation, rate and credit spread are stated assumptions, not market data as of each pricing date.

Cross-check note: each extraction was compared with a second extraction by a different model version, and any differing fields are recorded in the saved results. This is a weaker independence check than two genuinely different model services, and the audit outputs should be read with that limitation stated.

This is a risk analysis under stated assumptions, not investment advice.
