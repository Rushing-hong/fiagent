"""Tests for ESG tools (mocked network, no live calls)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import tools.esg as esg_tools
from market.esg_data import EsgDataError


def _load(raw: str) -> dict:
    return json.loads(raw)


@pytest.fixture
def sample_carbon_rows():
    return [
        {
            "market": "CN",
            "exchange": "湖北",
            "trade_date": "2024-01-02",
            "price": 45.0,
            "volume": 1000.0,
            "amount": 45000.0,
            "unit": "CNY_per_ton",
            "source": "akshare.energy_carbon_domestic",
        },
        {
            "market": "CN",
            "exchange": "上海",
            "trade_date": "2024-01-03",
            "price": 60.0,
            "volume": 500.0,
            "amount": 30000.0,
            "unit": "CNY_per_ton",
            "source": "akshare.energy_carbon_domestic",
        },
    ]


@pytest.fixture
def sample_reports():
    return [
        {
            "title": "2024年度ESG报告",
            "date": "2025-04-01",
            "url": "http://static.cninfo.com.cn/finalpage/2025-04-01/1.PDF",
            "code": "600519.SH",
            "name": "贵州茅台",
            "announcement_id": "1",
        }
    ]


def test_get_carbon_prices_ok(monkeypatch, sample_carbon_rows):
    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", lambda: sample_carbon_rows)
    tool = esg_tools.GetCarbonPricesTool()
    payload = _load(tool.execute({"limit": 10}, None))
    assert payload["ok"] is True
    assert payload["quality"] == "degraded"
    assert payload["data"]["total_rows"] == 2
    assert len(payload["data"]["latest"]) == 2


def test_get_carbon_prices_exchange_filter(monkeypatch, sample_carbon_rows):
    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", lambda: sample_carbon_rows)
    tool = esg_tools.GetCarbonPricesTool()
    payload = _load(tool.execute({"exchange": "湖北", "limit": 5}, None))
    assert payload["ok"] is True
    assert payload["data"]["total_rows"] == 1
    assert payload["data"]["latest"][0]["exchange"] == "湖北"


def test_get_carbon_prices_exchange_miss(monkeypatch, sample_carbon_rows):
    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", lambda: sample_carbon_rows)
    tool = esg_tools.GetCarbonPricesTool()
    payload = _load(tool.execute({"exchange": "EU"}, None))
    assert payload["ok"] is False
    assert "未找到" in payload["error"]


def test_get_carbon_prices_fetch_error(monkeypatch):
    def _boom():
        raise EsgDataError("akshare 未安装")

    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", _boom)
    tool = esg_tools.GetCarbonPricesTool()
    payload = _load(tool.execute({}, None))
    assert payload["ok"] is False
    assert "akshare" in payload["error"]


def test_search_esg_reports_ok(monkeypatch, sample_reports):
    monkeypatch.setattr(
        esg_tools,
        "search_cninfo_esg",
        lambda keyword, page_size, code=None: (sample_reports, "cninfo.hisAnnouncement", "normal"),
    )
    tool = esg_tools.SearchEsgReportsTool()
    payload = _load(tool.execute({"keyword": "ESG", "code": "600519.SH"}, None))
    assert payload["ok"] is True
    assert payload["quality"] == "normal"
    assert payload["data"]["count"] == 1
    assert payload["data"]["reports"][0]["title"].endswith("ESG报告")


def test_search_esg_reports_empty(monkeypatch):
    monkeypatch.setattr(
        esg_tools,
        "search_cninfo_esg",
        lambda keyword, page_size, code=None: ([], "cninfo.hisAnnouncement", "normal"),
    )
    tool = esg_tools.SearchEsgReportsTool()
    payload = _load(tool.execute({"keyword": "不存在的关键词xyz"}, None))
    assert payload["ok"] is True
    assert payload["data"]["count"] == 0
    assert "cninfo_hint" in payload["data"]


def test_search_esg_reports_error(monkeypatch):
    def _fail(keyword, page_size, code=None):
        raise EsgDataError("巨潮 ESG 公告检索失败: timeout")

    monkeypatch.setattr(esg_tools, "search_cninfo_esg", _fail)
    tool = esg_tools.SearchEsgReportsTool()
    payload = _load(tool.execute({"keyword": "ESG"}, None))
    assert payload["ok"] is False
    assert "巨潮" in payload["error"]


def test_get_esg_overview_ok(monkeypatch, sample_carbon_rows, sample_reports):
    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", lambda: sample_carbon_rows)
    monkeypatch.setattr(
        esg_tools,
        "search_cninfo_esg",
        lambda keyword, page_size, code=None: (sample_reports, "cninfo.hisAnnouncement", "normal"),
    )
    tool = esg_tools.GetEsgOverviewTool()
    payload = _load(tool.execute({"code": "600519.SH"}, None))
    assert payload["ok"] is True
    assert payload["data"]["carbon"]["latest"]
    assert payload["data"]["recent_reports"]
    assert "cninfo_usage" in payload["data"]


def test_get_esg_overview_partial_degraded(monkeypatch, sample_reports):
    def _no_carbon():
        raise EsgDataError("碳价不可用")

    monkeypatch.setattr(esg_tools, "fetch_carbon_prices", _no_carbon)
    monkeypatch.setattr(
        esg_tools,
        "search_cninfo_esg",
        lambda keyword, page_size, code=None: (sample_reports, "cninfo.hisAnnouncement", "normal"),
    )
    tool = esg_tools.GetEsgOverviewTool()
    payload = _load(tool.execute({"code": "600519.SH"}, None))
    assert payload["ok"] is True
    assert payload["quality"] == "degraded"
    assert payload["data"]["carbon"]["error"]


def test_search_cninfo_esg_parses_http(monkeypatch):
    from market import esg_data as mod

    fake = {
        "announcements": [
            {
                "secCode": "600519",
                "secName": "贵州茅台",
                "announcementTitle": "ESG报告",
                "announcementTime": 1704067200000,
                "adjunctUrl": "finalpage/2024-01-01/1.PDF",
                "announcementId": "99",
            }
        ]
    }

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return fake

    with patch("requests.post", return_value=FakeResp()):
        rows, source, quality = mod.search_cninfo_esg("ESG", 5)
    assert quality == "normal"
    assert source == "cninfo.hisAnnouncement"
    assert rows[0]["code"] == "600519.SH"
    assert rows[0]["url"].startswith("http://static.cninfo.com.cn/")


def test_fetch_carbon_prices_requires_ak(monkeypatch):
    from market import esg_data as mod

    monkeypatch.setattr(mod, "_AK_AVAILABLE", False)
    with pytest.raises(EsgDataError, match="akshare"):
        mod.fetch_carbon_prices()
