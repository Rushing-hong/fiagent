"""Offline tests for screen_peers board selection and percentiles."""

from __future__ import annotations

from tools.screen_peers import build_peer_stats, select_industry_board, _percentile_rank


def test_select_board_prefers_median_size():
    membership = [
        {"board_code": "BK0438", "board_name": "食品饮料"},
        {"board_code": "BK1575", "board_name": "白酒Ⅲ"},
        {"board_code": "BK0500", "board_name": "HS300_"},
    ]
    meta = {
        "BK0438": {"name": "食品饮料", "approx_n": 100},
        "BK1575": {"name": "白酒Ⅲ", "approx_n": 19},
        "BK9999": {"name": "其他", "approx_n": 50},
    }
    chosen = select_industry_board(membership, meta)
    # sizes 19 and 100 → median 59.5 → closer is 100? |100-59.5|=40.5, |19-59.5|=40.5 — tie
    # sort is stable by abs; both equal — first after sort may be either
    assert chosen["board_code"] in ("BK0438", "BK1575")
    assert chosen["selection"] == "auto"


def test_select_explicit_board_code():
    chosen = select_industry_board(
        [{"board_code": "BK1575", "board_name": "白酒Ⅲ"}],
        {"BK1575": {"name": "白酒Ⅲ", "approx_n": 19}},
        board_code="BK1575",
    )
    assert chosen["board_code"] == "BK1575"
    assert chosen["selection"] == "explicit"


def test_exclude_综合():
    membership = [
        {"board_code": "BK0001", "board_name": "综合"},
        {"board_code": "BK1575", "board_name": "白酒Ⅲ"},
    ]
    meta = {
        "BK0001": {"name": "综合", "approx_n": 80},
        "BK1575": {"name": "白酒Ⅲ", "approx_n": 19},
    }
    chosen = select_industry_board(membership, meta)
    assert chosen["board_code"] == "BK1575"


def test_build_peer_stats_excludes_st_and_neg_pe():
    peers = [
        {"code": "600519.SH", "name": "贵州茅台", "pe": 20.0, "pb": 8.0, "roe": 30.0, "market_cap": 20000},
        {"code": "000858.SZ", "name": "五粮液", "pe": 18.0, "pb": 4.0, "roe": 25.0, "market_cap": 5000},
        {"code": "000001.SZ", "name": "股票A", "pe": 15.0, "pb": 3.0, "roe": 12.0, "market_cap": 1000},
        {"code": "000002.SZ", "name": "股票B", "pe": 25.0, "pb": 5.0, "roe": 18.0, "market_cap": 800},
        {"code": "000003.SZ", "name": "股票C", "pe": 22.0, "pb": 6.0, "roe": 20.0, "market_cap": 700},
        {"code": "000004.SZ", "name": "*ST垃圾", "pe": 5.0, "pb": 1.0, "roe": 1.0, "market_cap": 50},
        {"code": "000005.SZ", "name": "亏损股", "pe": -10.0, "pb": 2.0, "roe": -5.0, "market_cap": 100},
    ]
    built = build_peer_stats(peers, "600519.SH")
    assert built["st_removed"] == 1
    assert built["loss_making_count"] == 1
    assert built["stats_available"] is True
    assert "PE" in built["stats"]
    assert built["stats"]["PE"]["target"] == 20.0
    assert built["target"]["code"] == "600519.SH"


def test_percentile_rank_mid():
    assert _percentile_rank([10, 20, 30, 40], 25) == 50.0
