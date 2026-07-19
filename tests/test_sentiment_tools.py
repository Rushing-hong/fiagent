"""Tests for guba sentiment + overnight return tools (mocked network)."""

from __future__ import annotations

import json
from unittest.mock import patch

from market.sentiment_data import (
    fetch_guba_posts,
    overnight_vs_intraday,
    score_guba_sentiment,
    summarize_overnight_intraday,
)
from tools.sentiment import GubaSentimentTool, OvernightReturnsTool


def _sample_guba_html(titles: list[str]) -> str:
    posts = [
        {
            "post_id": 1000 + i,
            "post_title": title,
            "post_publish_time": f"2026-07-{10 + i:02d} 09:00:00",
            "post_click_count": 100 + i,
            "post_comment_count": 5 + i,
            "post_user": {"user_nickname": f"user{i}"},
        }
        for i, title in enumerate(titles)
    ]
    payload = json.dumps({"re": posts}, ensure_ascii=False)
    return f"<html><script>article_list={payload};</script></html>"


def test_score_guba_sentiment_lexicon():
    posts = [
        {"title": "强烈看涨突破买入机会"},
        {"title": "利空下跌卖出逃顶"},
        {"title": "今日复盘记录"},
    ]
    scored = score_guba_sentiment(posts)
    assert scored["n_posts"] == 3
    assert scored["bull_hits"] == 1
    assert scored["bear_hits"] == 1
    assert scored["neutral_hits"] == 1
    assert -1.0 <= scored["score"] <= 1.0


def test_overnight_vs_intraday_math():
    rows = [
        {"trade_date": "2024-01-02", "open": 10.0, "close": 10.5},
        {"trade_date": "2024-01-03", "open": 10.8, "close": 10.2},
    ]
    series = overnight_vs_intraday(rows)
    assert len(series) == 2
    assert series[0]["overnight"] is None
    assert series[0]["intraday"] == round(10.5 / 10.0 - 1, 6)
    assert series[1]["overnight"] == round(10.8 / 10.5 - 1, 6)
    assert series[1]["intraday"] == round(10.2 / 10.8 - 1, 6)

    summary = summarize_overnight_intraday(series, last_n=2)
    assert summary["n_days"] == 2
    assert summary["overnight_mean"] is not None
    assert len(summary["recent"]) == 2


@patch("market.sentiment_data.throttled_get")
def test_fetch_guba_posts_parses_embedded_json(mock_get):
    mock_get.return_value.text = _sample_guba_html(["看好上涨", "风险下跌"])
    posts = fetch_guba_posts("600519.SH", page=1)
    assert len(posts) == 2
    assert posts[0]["title"] == "看好上涨"
    assert posts[0]["author"] == "user0"
    assert posts[0]["read_count"] == 100


@patch("tools.sentiment.fetch_guba_posts")
def test_get_guba_sentiment_tool(mock_fetch):
    mock_fetch.side_effect = [
        [{"title": "突破买入利好", "author": "a", "time": "t", "read_count": 1, "comment_count": 0}],
        [{"title": "跳水割肉", "author": "b", "time": "t", "read_count": 2, "comment_count": 1}],
    ]
    out = json.loads(GubaSentimentTool().execute({"code": "600519.SH", "pages": 2}, None))
    assert out["ok"] is True
    assert out["data"]["n_posts"] == 2
    assert "sentiment" in out["data"]
    assert mock_fetch.call_count == 2


@patch("tools.sentiment.fetch_one")
def test_calc_overnight_returns_tool(mock_fetch_one):
    mock_fetch_one.return_value = (
        [
            {"trade_date": "2024-01-02", "open": 100.0, "close": 102.0},
            {"trade_date": "2024-01-03", "open": 101.0, "close": 99.0},
        ],
        "tencent",
    )
    out = json.loads(
        OvernightReturnsTool().execute(
            {
                "codes": ["600519.SH"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-10",
                "last_n": 2,
            },
            None,
        )
    )
    assert out["ok"] is True
    entry = out["data"]["results"]["600519.SH"]
    assert entry["source"] == "tencent"
    assert entry["summary"]["n_days"] == 2


def test_get_guba_sentiment_rejects_empty_code():
    out = json.loads(GubaSentimentTool().execute({"code": ""}, None))
    assert out["ok"] is False
