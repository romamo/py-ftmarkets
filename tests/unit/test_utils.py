from datetime import datetime

from ftmarkets.utils import parse_date


def test_parse_date_iso():
    dt = parse_date("2023-01-01")
    assert dt == datetime(2023, 1, 1)


def test_parse_date_compact():
    dt = parse_date("20230101")
    assert dt == datetime(2023, 1, 1)


def test_parse_date_slashes():
    dt = parse_date("01/01/2023")
    assert dt == datetime(2023, 1, 1)


def test_parse_date_invalid():
    assert parse_date("invalid") is None
    assert parse_date("2023-13-01") is None
