"""resolve_secid / search suffix: A-share only."""

from unittest.mock import patch

from market.eastmoney import resolve_secid
from tools.stock_market import SearchSymbolTool, _as_bool, _clist_get


def test_resolve_secid_a_share():
    assert resolve_secid("600519.SH") == "1.600519"
    assert resolve_secid("000001.SZ") == "0.000001"
    assert resolve_secid("830799.BJ") == "0.830799"


def test_resolve_secid_rejects_hk_us():
    assert resolve_secid("00700.HK") is None
    assert resolve_secid("AAPL.US") is None
    assert resolve_secid("BTC-USDT") is None


def test_as_bool_parses_strings():
    assert _as_bool(True) is True
    assert _as_bool(False) is False
    assert _as_bool("false") is False
    assert _as_bool("true") is True
    assert _as_bool("0") is False
    assert _as_bool(None, True) is True


def test_search_symbol_maps_bj_and_skips_non_ashare():
    rows = [
        {"QuoteID": "1.600519", "Code": "600519", "Name": "贵州茅台"},
        {"QuoteID": "0.830799", "Code": "830799", "Name": "北交所样例"},
        {"QuoteID": "116.00700", "Code": "00700", "Name": "腾讯"},
        {"QuoteID": "105.AAPL", "Code": "AAPL", "Name": "Apple"},
    ]
    with patch("tools.stock_market.search_suggest", return_value=rows):
        out = SearchSymbolTool().execute({"query": "test", "limit": 10}, None)
    assert "600519.SH" in out
    assert "830799.BJ" in out
    assert "00700" not in out
    assert "AAPL" not in out


def test_clist_get_tries_next_host_on_empty_diff():
    empty = {"data": {"diff": []}}
    filled = {
        "data": {
            "diff": [{"f12": "000001", "f14": "x", "f2": 1, "f3": 1,
                      "f5": 1, "f6": 1, "f8": 1, "f9": 1, "f23": 1, "f20": 1}]
        }
    }
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "push2delay" in url:
            return filled
        return empty

    with patch("tools.stock_market.throttled_get_json", side_effect=fake_get):
        payload, host = _clist_get({"pn": "1"})
    assert "push2delay" in host
    assert payload is filled
    assert len(calls) == 2
