"""Business-day helpers used for payment dates stated as an offset."""

from datetime import date

import pytest

from quantproof.core.calendar import add_business_days, parse_business_day_lag


def test_add_business_days_skips_weekends():
    friday = date(2027, 10, 29)
    assert add_business_days(friday, 1) == date(2027, 11, 1)  # Monday
    assert add_business_days(friday, 5) == date(2027, 11, 5)  # next Friday
    assert add_business_days(date(2027, 11, 1), 3) == date(2027, 11, 4)  # Mon -> Thu
    assert add_business_days(date(2027, 10, 30), 0) == date(2027, 11, 1)  # Sat rolls to Mon
    assert add_business_days(friday, 0) == friday
    with pytest.raises(ValueError):
        add_business_days(friday, -1)


@pytest.mark.parametrize(
    "text, lag",
    [
        ("Call Payment Date: | The fifth business day following the Call Observation Date", 5),
        ("Call Settlement Date: | Three business days after the applicable call date", 3),
        ("Approximately the fifth business day following the applicable Observation Date", 5),
        ("payable on the 2nd business day after the review date", 2),
        ("paid 10 business days following each date", 10),
        ("the third business day prior to each coupon payment date", None),  # before, not after
        ("the Maturity Date", None),
    ],
)
def test_parse_business_day_lag(text, lag):
    assert parse_business_day_lag(text) == lag
