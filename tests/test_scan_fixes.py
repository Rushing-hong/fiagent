"""Regression tests for full-repo scan fixes."""

from __future__ import annotations

from unittest.mock import patch

from market.a_share_code import a_share_suffix, to_a_share_symbol
from market.eastmoney import resolve_secid, validate_a_share
from tools.run_python import RunPythonTool
from tools.web import _url_allowed


def test_bj_920_suffix_and_secid():
    assert a_share_suffix("920001") == ".BJ"
    assert a_share_suffix("830799") == ".BJ"
    assert a_share_suffix("600519") == ".SH"
    assert to_a_share_symbol("920001.SH") == "920001.BJ"
    assert to_a_share_symbol("920001") == "920001.BJ"
    assert validate_a_share("920001.SH") == "920001.BJ"
    assert resolve_secid("920001.SH") == "0.920001"
    assert resolve_secid("830799.BJ") == "0.830799"


def test_run_python_not_readonly():
    assert RunPythonTool.is_readonly is False


def test_url_allowed_blocks_literal_private():
    ok, _ = _url_allowed("http://127.0.0.1/")
    assert ok is False
    ok, _ = _url_allowed("http://10.0.0.1/x")
    assert ok is False


def test_url_allowed_blocks_resolved_loopback():
    with patch(
        "tools.web.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
    ):
        ok, _ = _url_allowed("https://evil.example/")
    assert ok is False


def test_calendar_refresh_invalidates_cache(tmp_path, monkeypatch):
    import market.trade_calendar as tc
    import pandas as pd
    from market.research_store import ResearchStore

    db = tmp_path / "r.db"
    store = ResearchStore(db)
    monkeypatch.setattr("market.research_store.get_store", lambda: store)
    store.replace_trade_calendar(["2024-01-02", "2024-01-03"])
    tc.invalidate_calendar_cache()
    assert tc.is_trading_day("2024-01-02") is True
    assert tc.is_trading_day("2099-01-01") is False

    class _Ak:
        @staticmethod
        def tool_trade_date_hist_sina():
            return pd.DataFrame({"trade_date": ["2099-01-01"]})

    monkeypatch.setitem(__import__("sys").modules, "akshare", _Ak())
    n = tc.refresh_calendar_cache(force=True)
    assert n == 1
    assert tc.is_trading_day("2099-01-01") is True
