# QuantProof

QuantProof reads the SEC pricing supplement (a 424B2 filing) of a structured note and shows, under stated assumptions, an independently computed estimated-value range next to the bank's own estimated value and the issue price, plus how and when the note can lose money. Meta models extract the note's terms with a filing quote for every field and a second model cross-checks the extraction; our own engine does all the maths, and no payoff is priced until its code passes a proof battery of closed-form, limiting-case and monotonicity checks. Every number in a report comes from an engine output or a cited filing value, never from a model.

## Install and run the tests

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The tests run offline and deterministically (fixed seeds, saved filing fixtures, recorded model replies).

## Run an analysis

```bash
python scripts/analyse.py tests/fixtures/filings/jpm_424b2-38582.htm \
    --reply tests/fixtures/extractions/jpm_424b2-38582.reply.json \
    --rate 0.037 --spread 0.006 --vols 0.24,0.17 \
    --dividends 0.012,0.013 --corr 0.6 --battery \
    --out report.md
```

This prices the saved JPMorgan filing offline from its recorded extraction. The market inputs are illustrative assumptions entered on the command line, not market data. See `--help` for live extraction and EDGAR download options.

## Validation and example

- [Validation report](docs/VALIDATION.md): what the proof battery proves, note by note.
- [Example analysis](docs/examples/jpm_38582_analysis.md): an end-to-end risk analysis of the saved JPMorgan note.

QuantProof is risk analysis under stated assumptions, not investment advice. The full README (method, assumptions, limitations, references) comes on Day 8; see [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md) in the meantime.
