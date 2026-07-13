"""一致预期修订追踪（研报 EPS 聚合降级版）。

无官方历史共识快照面板时：用东财逐篇研报 predictThisYearEps 按发布日
聚合成修订序列，并与财报实际 EPS（normalized）对比算简易 SUE。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from market.eastmoney import (
    F10_REPORT_URL,
    bare_a_share_code,
    fetch_datacenter,
    get_json,
    validate_a_share,
)
from market.eastmoney_indicator_fields import attach_normalized
from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool
from tools.stock_research import _REPORT_LIST_URL, _fetch_ths_consensus, _parse_reports


def _fetch_reports_window(code: str, days: int, page_size: int = 50) -> list[dict]:
    end = datetime.now()
    begin = end - timedelta(days=days)
    bare = bare_a_share_code(code)
    out: list[dict] = []
    for page in range(1, 6):
        payload = get_json(
            _REPORT_LIST_URL,
            params={
                "code": bare,
                "qType": "0",
                "pageSize": str(page_size),
                "pageNo": str(page),
                "beginTime": begin.strftime("%Y-%m-%d"),
                "endTime": end.strftime("%Y-%m-%d"),
            },
        )
        chunk = _parse_reports(payload)
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page_size:
            break
    return out


def _actual_eps_annual(code: str) -> list[dict[str, Any]]:
    rows = fetch_datacenter(
        "RPT_F10_FINANCE_MAINFINADATA",
        filter_expr=f'(SECUCODE="{code}")',
        sort_columns="REPORT_DATE",
        page_size=20,
        url=F10_REPORT_URL,
        source="F10",
        client="PC",
    )
    annual = [
        r for r in rows
        if str(r.get("REPORT_DATE", ""))[:10].endswith("-12-31")
    ] or rows
    annual = attach_normalized(annual[:8], statement="indicators")
    out = []
    for r in annual:
        norm = r.get("normalized") if isinstance(r.get("normalized"), dict) else {}
        eps = to_float(norm.get("eps"))
        if eps is None:
            continue
        out.append({
            "report_date": str(r.get("REPORT_DATE", ""))[:10],
            "year": str(r.get("REPORT_DATE", ""))[:4],
            "eps": eps,
        })
    return out


def _revision_series(reports: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    """Daily mean this_year EPS from reports → revision stats."""
    by_day: dict[str, list[float]] = defaultdict(list)
    up = down = 0
    last_by_broker: dict[str, float] = {}
    for r in reports:
        eps = (r.get("eps_forecast") or {}).get("this_year")
        if eps is None:
            continue
        day = r.get("publish_date") or ""
        if not day:
            continue
        by_day[day].append(float(eps))
        broker = str(r.get("brokerage") or r.get("analyst") or "unknown")
        prev = last_by_broker.get(broker)
        if prev is not None:
            if eps > prev * 1.001:
                up += 1
            elif eps < prev * 0.999:
                down += 1
        last_by_broker[broker] = float(eps)

    days = sorted(by_day.keys())
    series = [
        {
            "date": d,
            "mean_eps": round(sum(by_day[d]) / len(by_day[d]), 4),
            "n_reports": len(by_day[d]),
        }
        for d in days
    ]

    momentum: dict[str, Any] = {
        "upgrades": up,
        "downgrades": down,
        "net_revisions": up - down,
        "n_reports_with_eps": sum(len(v) for v in by_day.values()),
        "signal": "neutral",
    }
    if series:
        latest = series[-1]["mean_eps"]
        # compare last 30d mean vs prior 60d
        cutoff_recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        cutoff_old = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        recent = [s["mean_eps"] for s in series if s["date"] >= cutoff_recent]
        older = [
            s["mean_eps"] for s in series
            if cutoff_old <= s["date"] < cutoff_recent
        ]
        if recent and older:
            r_mean = sum(recent) / len(recent)
            o_mean = sum(older) / len(older)
            chg = (r_mean / o_mean - 1.0) if o_mean else 0.0
            momentum["revision_pct_30d_vs_prior"] = round(chg * 100, 2)
            if chg > 0.01 or (up - down) >= 2:
                momentum["signal"] = "bullish_revision"
            elif chg < -0.01 or (down - up) >= 2:
                momentum["signal"] = "bearish_revision"
        momentum["latest_mean_eps"] = latest
    return series, momentum


def _sue_table(actual: list[dict], consensus: list[dict], series: list[dict]) -> list[dict]:
    """Simple surprise vs THS consensus or latest report mean."""
    cons_map = {}
    for c in consensus:
        y = str(c.get("year") or "")
        e = to_float(c.get("eps"))
        if y and e is not None:
            cons_map[y] = e
    fallback = series[-1]["mean_eps"] if series else None
    # SUE std from historical surprises if >=2
    surprises: list[float] = []
    rows = []
    for a in actual:
        y = a["year"]
        exp = cons_map.get(y, fallback)
        if exp is None or exp == 0:
            continue
        surprise = (a["eps"] - exp) / abs(exp)
        surprises.append(surprise)
        rows.append({
            "year": y,
            "actual_eps": a["eps"],
            "expected_eps": exp,
            "surprise_pct": round(surprise * 100, 2),
            "expected_source": "ths" if y in cons_map else "report_mean",
        })
    if len(surprises) >= 2:
        import statistics
        sd = statistics.pstdev(surprises) or 1e-9
        for r, s in zip(rows, surprises):
            r["sue"] = round(s / sd, 2)
    else:
        for r in rows:
            r["sue"] = None
    return rows


class TrackConsensusTool(BaseTool):
    name = "track_consensus"
    summary = "一致预期修订/SUE（研报聚合）"
    description = (
        "无历史共识快照时的降级实现：拉取东财逐篇研报 EPS 预测，按发布日聚合修订动量；"
        "并用财报实际 EPS vs THS/研报均值估算简易 SUE。\n"
        "非机构级一致预期修订面板；quality 标记 degraded。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "days": {
                "type": "integer",
                "default": 365,
                "description": "研报回溯天数",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = validate_a_share(str(args.get("code") or ""))
        if code is None:
            return err("需要有效的 A 股代码")
        days = clamp_int(args.get("days"), 365, 90, 1095)
        try:
            reports = _fetch_reports_window(code, days=days)
        except Exception as exc:
            return err(f"研报拉取失败: {exc}")
        try:
            actual = _actual_eps_annual(code)
        except Exception:
            actual = []
        consensus = _fetch_ths_consensus(code)
        series, momentum = _revision_series(reports)
        # Persist snapshots for true multi-day panel over sessions
        try:
            from market.research_store import get_store
            store = get_store()
            if consensus:
                store.save_consensus(code, source="ths", points=consensus)
            if series:
                store.save_consensus(
                    code,
                    source="report_mean",
                    points=[{
                        "year": "this_year",
                        "eps": series[-1]["mean_eps"],
                        "n_reports": series[-1]["n_reports"],
                    }],
                )
            hist = store.load_consensus_history(code, days=days)
        except Exception:
            hist = []
        sue = _sue_table(actual, consensus, series)
        if not reports and not consensus and not actual:
            return err(f"无可用数据: {code}")
        return ok(
            {
                "code": code,
                "revision_series": series[-40:],
                "revision_momentum": momentum,
                "sue_table": sue,
                "consensus_eps_ths": consensus,
                "local_consensus_history": hist[-60:],
                "n_reports": len(reports),
                "note": (
                    "基于逐篇研报 EPS 聚合的修订代理；local_consensus_history 随调用累积。"
                    "非官方付费共识点位面板。SUE 在样本不足时可能无 σ 标准化。"
                ),
            },
            market="a_share",
            source="eastmoney+ths+local",
            tool="track_consensus",
            quality="degraded",
        )
