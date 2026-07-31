"""Unit tests for get_limit_board (three Eastmoney pools, mocked)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from market import akshare_data as m


def _zt_df():
    return pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "涨跌幅": 10.01,
                "最新价": 12.5,
                "最后封板时间": "09:35:00",
                "连板数": 2,
                "炸板次数": 0,
                "换手率": 3.2,
                "所属行业": "银行",
            }
        ]
    )


def _zb_df():
    return pd.DataFrame(
        [
            {
                "代码": "000002",
                "名称": "万科A",
                "涨跌幅": 8.5,
                "最新价": 9.1,
                "首次封板时间": "10:00:00",
                "连板数": 1,
                "炸板次数": 2,
                "换手率": 5.0,
                "所属行业": "房地产",
            }
        ]
    )


def _dt_df():
    return pd.DataFrame(
        [
            {
                "代码": "000003",
                "名称": "跌停样例",
                "涨跌幅": -10.0,
                "最新价": 3.3,
                "最后封板时间": "14:00:00",
                "连续跌停": 1,
                "换手率": 1.1,
                "所属行业": "综合",
            }
        ]
    )


@pytest.fixture
def fake_ak(monkeypatch):
    ak = SimpleNamespace(
        stock_zt_pool_em=lambda date: _zt_df(),
        stock_zt_pool_zbgc_em=lambda date: _zb_df(),
        stock_zt_pool_dtgc_em=lambda date: _dt_df(),
    )
    monkeypatch.setattr(m, "_require_ak", lambda: None)
    monkeypatch.setitem(__import__("sys").modules, "akshare", ak)
    return ak


def test_get_limit_board_three_pools(fake_ak, monkeypatch):
    monkeypatch.setattr(
        "market.trade_calendar.latest_trading_day", lambda: "2026-07-21"
    )
    raw = m.get_limit_board("2026-07-21")
    env = json.loads(raw)
    assert env["ok"] is True
    assert env["quality"] == "normal"
    data = env["data"]
    assert data["date"] == "2026-07-21"
    assert data["limit_up_count"] == 1
    assert data["broken_board_count"] == 1
    assert data["limit_down_count"] == 1
    assert data["limit_up"][0]["code"] == "000001"
    assert data["broken_board"][0]["type"] == "limit_up_broken"
    assert data["limit_down"][0]["change_pct"] == -10.0
    assert data["pools"]["limit_down"] == "stock_zt_pool_dtgc_em"


def test_get_limit_board_partial_when_one_pool_fails(fake_ak, monkeypatch):
    monkeypatch.setattr(
        "market.trade_calendar.latest_trading_day", lambda: "2026-07-21"
    )

    def boom(date):
        raise RuntimeError("dtgc down")

    fake_ak.stock_zt_pool_dtgc_em = boom
    env = json.loads(m.get_limit_board("2026-07-21"))
    assert env["ok"] is True
    assert env["quality"] == "partial"
    data = env["data"]
    assert data["limit_up_count"] == 1
    assert data["limit_down_count"] == 0
    assert "limit_down" in (data.get("pool_errors") or {})
