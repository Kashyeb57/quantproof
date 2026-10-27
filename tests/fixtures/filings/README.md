# Filing fixtures

Real SEC EDGAR filings (public records) saved for offline, deterministic tests.

| File | Filing | Source |
|---|---|---|
| `jpm_424b2-38582.htm` | JPMorgan preliminary pricing supplement, Review Notes linked to the lesser performing of the Russell 2000 and S&P 500, CUSIP 48136LQH5, dated Nov 17, 2025 | https://www.sec.gov/Archives/edgar/data/19617/000183988225066171/jpm_424b2-38582.htm |
| `jpm_424b2-38582_files/image2.gif`, `image3.gif` | Its payoff flowcharts (automatic call; payment at maturity) | same filing |
| `jpm_424b2-38582_files/image4.jpg` | Its Russell 2000 historical chart | same filing |

Saved from a browser ("Webpage, Complete"), byte-for-byte. The `.htm` is not UTF-8: decode it with
the charset the filing declares (see `ingest/`), otherwise symbols such as "×" are garbled.
