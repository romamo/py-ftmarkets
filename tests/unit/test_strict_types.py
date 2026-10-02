import pytest
from pydantic import ValidationError
from pydantic_extra_types.currency_code import Currency
from pydantic_market_data.models import ISIN, Price

from ftmarkets.extract.schemas import Symbol


def test_symbol_valid():
    t = Symbol(root="AAPL")
    assert str(t) == "AAPL"


def test_isin_valid():
    i = ISIN(root="US0378331005")
    assert str(i) == "US0378331005"


def test_currency_valid():
    # pydantic-extra-types Currency often validates on model instantiation
    # but let's see if it works as a standalone if it's a class
    c = Currency("USD")
    assert str(c) == "USD"


def test_price_valid():
    p = Price(root=150.5)
    assert p.root == 150.5


def test_price_invalid():
    with pytest.raises(ValidationError):
        Price(root="not a float")
