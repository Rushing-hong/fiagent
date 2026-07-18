"""screen_market fallback / ascending helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.stock_market import ScreenMarketTool, _rows_from_push2


def test_rows_from_push2_maps_fields():
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f14": "平安银行",
                    "f2": 10.5,
                    "f3": -2.1,
                    "f5": 1,
                    "f6": 2,
                    "f8": 3,
                    "f9": 4,
                    "f23": 5,
                    "f20": 6,
                }
            ]
        }
    }
    rows = _rows_from_push2(payload, 10)
    assert len(rows) == 1
    assert rows[0]["code"] == "000001"
    assert rows[0]["change_pct"] == -2.1


def test_screen_market_falls_back_when_clist_fails():
    tool = ScreenMarketTool()
    fake_stocks = [
        {
            "code": "300001",
            "name": "测试",
            "price": 1.0,
            "change_pct": -9.9,
            "volume": 1,
            "amount": 1,
            "turnover_rate": 1,
            "pe": 1,
            "pb": 1,
            "market_cap": 1,
        }
    ]
    with patch("tools.stock_market._clist_get", side_effect=RuntimeError("boom")):
        with patch("tools.stock_market._screen_via_akshare", return_value=fake_stocks):
            out = tool.execute(
                {"market": "a", "sort_by": "change_pct", "top_n": 5, "ascending": True},
                ctx=None,
            )
    assert '"ok": true' in out or '"ok":true' in out.replace(" ", "")
    assert "akshare" in out
    assert "degraded" in out
    assert "ascending" in out


def test_defaults_raised():
    from core import loop

    assert loop.MAX_TOOL_ROUNDS >= 20
    assert loop.DOOM_LOOP_AT >= 3
