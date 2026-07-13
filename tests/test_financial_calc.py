"""Unit tests for Dupont chain substitution and field normalize (offline)."""

from __future__ import annotations

from market.eastmoney_indicator_fields import normalize_row
from tools.financial_calc import _chain_substitution, _ni_near_zero, _yoy_growth


def test_chain_substitution_identity():
    base = {"npm": 0.085, "at": 0.85, "em": 2.10}
    curr = {"npm": 0.092, "at": 0.88, "em": 2.20}
    attr = _chain_substitution(base, curr)
    expected_total = (0.092 * 0.88 * 2.20) - (0.085 * 0.85 * 2.10)
    assert abs(attr["total_change"] - expected_total) < 1e-12
    assert abs(
        attr["npm_contribution"] + attr["at_contribution"] + attr["em_contribution"]
        - attr["total_change"]
    ) < 1e-12


def test_normalize_indicators_aliases():
    row = {
        "REPORT_DATE": "2024-12-31 00:00:00",
        "TOTALOPERATEREVE": 1000.0,
        "PARENTNETPROFIT": 100.0,
        "TOTAL_ASSETS_PK": 2000.0,
        "TOTAL_EQUITY_PK": 800.0,
        "NETCASH_OPERATE_PK": 90.0,
        "KCFJCXSYJLR": 95.0,
        "ROEJQ": 12.5,
        "GOODWILL": None,
    }
    n = normalize_row(row, statement="indicators")
    assert n["report_date"] == "2024-12-31"
    assert n["revenue"] == 1000.0
    assert n["net_income"] == 100.0
    assert n["cfo"] == 90.0
    assert n["goodwill"] == 0.0
    assert n["roe"] == 12.5


def test_interest_debt_sum():
    row = {
        "REPORT_DATE": "2024-12-31",
        "SHORT_LOAN": 10.0,
        "LONG_LOAN": 20.0,
        "BOND_PAYABLE": 5.0,
        "SHORT_BOND_PAYABLE": None,
        "LEASE_LIAB": 1.0,
        "BORROW_FUND": None,
        "GOODWILL": 0,
        "MONETARYFUNDS": 50.0,
        "INVENTORY": 3.0,
        "ACCOUNTS_RECE": 2.0,
        "TOTAL_PARENT_EQUITY": 100.0,
    }
    n = normalize_row(row, statement="balance")
    assert n["interest_debt"] == 36.0
    assert n["cash"] == 50.0
    assert n["total_equity"] == 100.0


def test_ni_near_zero_and_growth():
    assert _ni_near_zero(-1.0, 1000.0) is True
    assert _ni_near_zero(0.5, 1000.0) is True  # 0.05%
    assert _ni_near_zero(10.0, 1000.0) is False
    assert _yoy_growth(120.0, 100.0) == 0.2
    assert _yoy_growth(120.0, 0.0) is None
