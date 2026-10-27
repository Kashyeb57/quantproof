"""Shared fixtures.

`jpm_38582_termsheet` is the term sheet of the Day 1 test filing (JPMorgan preliminary pricing
supplement, CUSIP 48136LQH5): a worst-of autocallable on the Russell 2000 and S&P 500, built by
hand from the filing so tests do not depend on the extraction agent. Quotes are copied verbatim.
https://www.sec.gov/Archives/edgar/data/19617/000183988225066171/jpm_424b2-38582.htm
"""

from datetime import date

import pytest

from quantproof.core import (
    Autocall,
    AutocallObservation,
    BarrierObservation,
    Citation,
    Cited,
    Dependency,
    Downside,
    DownsideKind,
    TermSheet,
    Underlying,
)

KEY = "Key Terms PS-1"

REVIEW_DATES = [
    date(2026, 12, 2), date(2027, 2, 25), date(2027, 5, 25), date(2027, 8, 25), date(2027, 11, 26),
    date(2028, 2, 25), date(2028, 5, 25), date(2028, 8, 25), date(2028, 11, 27), date(2029, 2, 26),
    date(2029, 5, 25), date(2029, 8, 27), date(2029, 11, 26), date(2030, 2, 25), date(2030, 5, 28),
    date(2030, 8, 26), date(2030, 11, 25),
]
PAYMENT_DATES = [
    date(2026, 12, 8), date(2027, 3, 2), date(2027, 5, 28), date(2027, 8, 30), date(2027, 12, 1),
    date(2028, 3, 1), date(2028, 5, 31), date(2028, 8, 30), date(2028, 11, 30), date(2029, 3, 1),
    date(2029, 5, 31), date(2029, 8, 30), date(2029, 11, 29), date(2030, 2, 28), date(2030, 5, 31),
    date(2030, 8, 29), date(2030, 11, 29),
]
# Minimum call premiums: 9.75% stepping up 2.4375% per review date ("at least ...").
CALL_PREMIUMS = [97.5 + 24.375 * k for k in range(17)]

# Every quote below shows its value (the quote verifier checks that, not just that the text
# exists). Lists are quoted in full; the last call settlement date is "the Maturity Date", so its
# quote adds the sentence that states that date.
REVIEW_DATES_LINE = (
    "Review Dates*: December 2, 2026, February 25, 2027, May 25, 2027, August 25, 2027, "
    "November 26, 2027, February 25, 2028, May 25, 2028, August 25, 2028, November 27, 2028, "
    "February 26, 2029, May 25, 2029, August 27, 2029, November 26, 2029, February 25, 2030, "
    "May 28, 2030, August 26, 2030 and November 25, 2030 (final Review Date)"
)
SETTLEMENT_DATES_LINE = (
    "Call Settlement Dates*: December 8, 2026, March 2, 2027, May 28, 2027, August 30, 2027, "
    "December 1, 2027, March 1, 2028, May 31, 2028, August 30, 2028, November 30, 2028, "
    "March 1, 2029, May 31, 2029, August 30, 2029, November 29, 2029, February 28, 2030, "
    "May 31, 2030, August 29, 2030 and the Maturity Date ... Maturity Date*: November 29, 2030"
)
RTY_CLOSE = "The closing level of the Russell 2000® Index on November 17, 2025 was 2,341.377"
SPX_CLOSE = "The closing level of the S&P 500® Index on November 17, 2025 was 6,672.41"
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
            "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
            "final"]


def c(value, quote, section=KEY):
    return Cited(value, Citation(quote, section))


@pytest.fixture
def jpm_38582_termsheet() -> TermSheet:
    observations = [
        AutocallObservation(
            review_date=c(r, REVIEW_DATES_LINE),
            payment_date=c(p, SETTLEMENT_DATES_LINE),
            call_amount=c(1000 + prem, f"{ordinal} Review Date: at least {prem / 10:.4f}% × $1,000"),
        )
        for r, p, prem, ordinal in zip(REVIEW_DATES, PAYMENT_DATES, CALL_PREMIUMS, ORDINALS)
    ]
    return TermSheet(
        issuer=c("JPMorgan Chase Financial Company LLC",
                 "Issuer: JPMorgan Chase Financial Company LLC"),
        guarantor=c("JPMorgan Chase & Co.", "Guarantor: JPMorgan Chase & Co."),
        cusip=c("48136LQH5", "CUSIP: 48136LQH5", "Cover"),
        is_preliminary=c(True, "PRELIMINARY PRICING SUPPLEMENT", "Cover"),
        underlyings=[
            Underlying(name=c("Russell 2000 Index",
                               "The Russell 2000® Index (Bloomberg ticker: RTY)"),
                       ticker=c("RTY", "Bloomberg ticker: RTY"),
                       reference_level=c(2341.377, RTY_CLOSE, "PS-7"),
                       reference_date=c(date(2025, 11, 17), RTY_CLOSE, "PS-7")),
            Underlying(name=c("S&P 500 Index", "The S&P 500® Index (Bloomberg ticker: SPX)"),
                       ticker=c("SPX", "Bloomberg ticker: SPX"),
                       reference_level=c(6672.41, SPX_CLOSE, "PS-7"),
                       reference_date=c(date(2025, 11, 17), SPX_CLOSE, "PS-7")),
        ],
        dependency=c(Dependency.WORST_OF, "Lesser Performing Index Return"),
        notional=c(1000.0, "Minimum denominations of $1,000", "Cover"),
        issue_price=c(1000.0, "Per note | $1,000", "Cover"),
        pricing_date=c(date(2025, 11, 25), "Pricing Date: On or about November 25, 2025"),
        issue_date=c(date(2025, 11, 28), "On or about November 28, 2025"),
        maturity_date=c(date(2030, 11, 29), "Maturity Date*: November 29, 2030"),
        final_review_date=c(date(2030, 11, 25), "November 25, 2030 (final Review Date)"),
        autocall=Autocall(trigger=c(1.0, "Call Value: With respect to each Index, 100.00%"),
                          observations=observations, amounts_are_minimums=True),
        downside=Downside(
            kind=c(DownsideKind.BARRIER, "$1,000 + ($1,000 × Lesser Performing Index Return)"),
            level=c(0.70, "Barrier Amount: With respect to each Index, 70.00%"),
            observation=c(BarrierObservation.FINAL_ONLY,
                          "Final Value: With respect to each Index, the closing level of that "
                          "Index on the final Review Date"),
            gearing=c(1.0, "you will lose 1% of the principal amount", "PS-4"),
        ),
        estimated_value=c(954.0, "approximately $954.00 per $1,000 principal amount note",
                          "Cover"),
        estimated_value_floor=c(940.0, "will not be less than $940.00 per $1,000", "Cover"),
        max_selling_commission=c(33.5, "In no event will these selling commissions exceed $33.50",
                                 "Cover"),
        missing_or_ambiguous=["Initial Values are set on the Pricing Date (not yet known)"],
    )
