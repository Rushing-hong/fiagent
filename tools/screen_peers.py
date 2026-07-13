"""同业估值对比：东财行业板块成分 + PE/PB/ROE/市值分位。"""

from __future__ import annotations

import logging
import statistics
from typing import Any

from market.eastmoney import get_json, push2_diff_rows, resolve_secid, validate_a_share
from market.envelope import err, ok, to_float
from market.http import throttled_get_json
from tools.base import BaseTool
from tools.stock_disclosure import SectorInfoTool

logger = logging.getLogger(__name__)

_CLIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)
_MEMBERSHIP_URL = "https://push2.eastmoney.com/api/qt/slist/get"
_PEER_FIELDS = "f2,f3,f9,f12,f14,f20,f23,f37"
_FS_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# 指数/主题/风格等非行业可比板（membership 噪声）
_EXCLUDE_NAME_SUBSTR = (
    "综合",
    "沪股通",
    "深股通",
    "融资融券",
    "机构重仓",
    "证金",
    "MSCI",
    "富时",
    "普尔",
    "HS300",
    "上证",
    "深证",
    "央视",
    "指数",
    "风格",
    "大盘股",
    "权重股",
    "龙头",
    "百元股",
    "热股",
    "茅指数",
    "超级品牌",
    "西部大开发",
    "央国企",
    "电商概念",
    "概念",  # 概念板优先排除；行业板名一般不含「概念」
)

_PE_OUTLIER_HI = 200.0
_TIMEOUT_S = 10.0


def _clist_get(params: dict[str, Any], *, timeout: float = _TIMEOUT_S) -> Any:
    """push2 → push2delay 回退。"""
    last_exc: Exception | None = None
    for url in _CLIST_URLS:
        host = url.split("//", 1)[1].split("/", 1)[0]
        try:
            return throttled_get_json(
                url,
                host_key=host,
                min_interval=1.0,
                params=params,
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning("clist fail %s: %s", host, exc)
            continue
    raise RuntimeError(str(last_exc) if last_exc else "clist unavailable")


def _market_cap_yi(raw: Any) -> float | None:
    """push2 f20：元 → 亿元；若已是亿量级则原样。"""
    v = to_float(raw)
    if v is None:
        return None
    if abs(v) >= 1e6:  # 明显是元
        return round(v / 1e8, 4)
    return round(v, 4)


def _is_st(name: str) -> bool:
    return "ST" in name or "*ST" in name


def _suffix_code(bare: str) -> str:
    bare = bare.strip()
    if "." in bare:
        return bare.upper()
    if bare.startswith(("5", "6", "9")):
        return f"{bare}.SH"
    if bare.startswith(("0", "1", "2", "3")):
        return f"{bare}.SZ"
    if bare.startswith(("4", "8")):
        return f"{bare}.BJ"
    return bare


def _percentile_rank(sorted_vals: list[float], x: float) -> float:
    """经验分位：小于 x 的比例（0–100）。"""
    if not sorted_vals:
        return float("nan")
    below = sum(1 for v in sorted_vals if v < x)
    equal = sum(1 for v in sorted_vals if v == x)
    return round((below + 0.5 * equal) / len(sorted_vals) * 100, 2)


def _quantile_stats(values: list[float], target: float | None) -> dict[str, Any] | None:
    if len(values) < 5:
        return None
    s = sorted(values)
    out: dict[str, Any] = {
        "p25": round(statistics.quantiles(s, n=4)[0], 4),
        "median": round(statistics.median(s), 4),
        "p75": round(statistics.quantiles(s, n=4)[2], 4),
        "n": len(s),
    }
    if target is not None:
        out["target"] = target
        out["percentile_rank"] = _percentile_rank(s, target)
    return out


def _fetch_industry_board_meta() -> dict[str, dict[str, Any]]:
    """东财行业板块 code → {name, approx_n}。分页拉全。"""
    meta: dict[str, dict[str, Any]] = {}
    pn = 1
    total = None
    while pn <= 10:
        payload = _clist_get(
            {
                "fs": "m:90+t:2",
                "fields": "f12,f14,f104,f105",
                "pn": str(pn),
                "pz": "100",
                "po": "1",
                "np": "1",
                "fltt": "2",
                "fid": "f12",
            }
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and total is None:
            total = to_float(data.get("total"))
        rows = push2_diff_rows(payload)
        if not rows:
            break
        for r in rows:
            code = str(r.get("f12") or "")
            if not code:
                continue
            up = to_float(r.get("f104")) or 0.0
            down = to_float(r.get("f105")) or 0.0
            meta[code] = {
                "name": str(r.get("f14") or ""),
                "approx_n": int(up + down),
            }
        if total is not None and len(meta) >= int(total):
            break
        pn += 1
    return meta


def _fetch_board_constituents(board_code: str) -> list[dict[str, Any]]:
    payload = _clist_get(
        {
            "fs": f"b:{board_code}",
            "fields": _PEER_FIELDS,
            "pn": "1",
            "pz": "500",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "fid": "f20",
        }
    )
    rows = push2_diff_rows(payload)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("f12"):
            continue
        bare = str(r.get("f12"))
        name = str(r.get("f14") or "")
        out.append({
            "code": _suffix_code(bare),
            "name": name,
            "pe": to_float(r.get("f9")),
            "pb": to_float(r.get("f23")),
            "roe": to_float(r.get("f37")),
            "market_cap": _market_cap_yi(r.get("f20")),
            "price": to_float(r.get("f2")),
            "change_pct": to_float(r.get("f3")),
        })
    return out


def _fetch_membership(code: str) -> list[dict[str, str]]:
    """直接拉 membership，避免二次封装。失败则回退 SectorInfoTool。"""
    secid = resolve_secid(code)
    if secid:
        try:
            payload = get_json(
                _MEMBERSHIP_URL,
                params={
                    "secid": secid,
                    "spt": "3",
                    "pi": "0",
                    "pz": "100",
                    "fields": "f12,f13,f14,f3,f2",
                    "fltt": "2",
                    "po": "1",
                },
            )
            boards = []
            for raw in push2_diff_rows(payload):
                if isinstance(raw, dict) and raw.get("f12"):
                    boards.append({
                        "board_code": str(raw.get("f12")),
                        "board_name": str(raw.get("f14") or ""),
                    })
            if boards:
                return boards
        except Exception as exc:
            logger.warning("membership direct fail: %s", exc)
    raw = SectorInfoTool().execute({"code": code, "mode": "membership"}, None)
    import json

    env = json.loads(raw)
    if not env.get("ok"):
        return []
    return [
        {"board_code": b["board_code"], "board_name": b.get("board_name") or ""}
        for b in (env.get("data") or {}).get("boards") or []
        if b.get("board_code")
    ]


def _name_excluded(name: str) -> bool:
    return any(s in name for s in _EXCLUDE_NAME_SUBSTR)


def select_industry_board(
    membership: list[dict[str, str]],
    industry_meta: dict[str, dict[str, Any]],
    *,
    board_code: str | None = None,
) -> dict[str, Any]:
    """按方案：行业板 ∩ membership → 剔综合 → 规模 5–200 → 最接近中位数。"""
    if board_code:
        code = board_code.strip().upper()
        name = industry_meta.get(code, {}).get("name") or code
        for m in membership:
            if m["board_code"] == code:
                name = m.get("board_name") or name
                break
        return {"board_code": code, "board_name": name, "selection": "explicit"}

    candidates: list[dict[str, Any]] = []
    for m in membership:
        bc = m["board_code"]
        if bc not in industry_meta:
            continue
        name = m.get("board_name") or industry_meta[bc].get("name") or bc
        if _name_excluded(name):
            continue
        approx = industry_meta[bc].get("approx_n") or 0
        candidates.append({
            "board_code": bc,
            "board_name": name,
            "approx_n": approx,
        })

    # 无行业交集：从 membership 里用名称启发式兜底
    if not candidates:
        for m in membership:
            name = m.get("board_name") or ""
            if _name_excluded(name):
                continue
            candidates.append({
                "board_code": m["board_code"],
                "board_name": name,
                "approx_n": 0,
            })

    sized = [c for c in candidates if 5 <= (c["approx_n"] or 0) <= 200]
    pool = sized or candidates
    if not pool:
        raise ValueError("无法从 membership 解析行业板块，请传 board_code")

    sizes = sorted(c["approx_n"] for c in pool if c["approx_n"])
    if sizes:
        med = statistics.median(sizes)
        pool.sort(key=lambda c: abs((c["approx_n"] or med) - med))
    else:
        # 无规模信息：优先名称更短/更具体（白酒Ⅲ < 食品饮料 用长度启发式弱）
        pool.sort(key=lambda c: len(c["board_name"]))

    chosen = pool[0]
    chosen["selection"] = "auto"
    chosen["candidates"] = [
        {"board_code": c["board_code"], "board_name": c["board_name"], "approx_n": c["approx_n"]}
        for c in pool[:5]
    ]
    return chosen


def _degraded_universe() -> list[dict[str, Any]]:
    """回退：全市场 PE 排序 top500。"""
    payload = _clist_get(
        {
            "pn": "1",
            "pz": "500",
            "po": "1",
            "fid": "f9",
            "fs": _FS_A,
            "fields": _PEER_FIELDS,
            "fltt": "2",
            "np": "1",
        }
    )
    rows = push2_diff_rows(payload)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("f12"):
            continue
        bare = str(r.get("f12"))
        name = str(r.get("f14") or "")
        out.append({
            "code": _suffix_code(bare),
            "name": name,
            "pe": to_float(r.get("f9")),
            "pb": to_float(r.get("f23")),
            "roe": to_float(r.get("f37")),
            "market_cap": _market_cap_yi(r.get("f20")),
        })
    return out


def build_peer_stats(
    peers: list[dict[str, Any]],
    target_code: str,
    *,
    exclude_st: bool = True,
) -> dict[str, Any]:
    """清洗 + 分位统计（可单测）。"""
    target_code = target_code.upper()
    cleaned: list[dict[str, Any]] = []
    st_removed = 0
    for p in peers:
        name = p.get("name") or ""
        if exclude_st and _is_st(name):
            st_removed += 1
            continue
        cleaned.append(p)

    target = next((p for p in cleaned if p["code"].upper() == target_code), None)
    if target is None:
        # 目标可能被 ST 过滤；仍从原列表找
        target = next((p for p in peers if p["code"].upper() == target_code), None)

    loss_making = sum(1 for p in cleaned if p.get("pe") is not None and p["pe"] < 0)
    pe_outlier = sum(
        1 for p in cleaned
        if p.get("pe") is not None and (p["pe"] < 0 or p["pe"] > _PE_OUTLIER_HI)
    )

    pe_vals = [
        p["pe"] for p in cleaned
        if p.get("pe") is not None and 0 < p["pe"] <= _PE_OUTLIER_HI
    ]
    pb_vals = [
        p["pb"] for p in cleaned
        if p.get("pb") is not None and p["pb"] > 0
    ]
    roe_vals = [p["roe"] for p in cleaned if p.get("roe") is not None]

    t_pe = target.get("pe") if target else None
    t_pb = target.get("pb") if target else None
    t_roe = target.get("roe") if target else None
    if t_pe is not None and not (0 < t_pe <= _PE_OUTLIER_HI):
        t_pe_for_rank = None
    else:
        t_pe_for_rank = t_pe

    stats: dict[str, Any] = {
        "PE": _quantile_stats(pe_vals, t_pe_for_rank),
        "PB": _quantile_stats(pb_vals, t_pb if t_pb and t_pb > 0 else None),
        "ROE": _quantile_stats(roe_vals, t_roe),
    }
    # 去掉 None 统计项
    stats = {k: v for k, v in stats.items() if v is not None}

    return {
        "peers": cleaned,
        "target": target,
        "constituent_count": len(cleaned),
        "st_removed": st_removed,
        "loss_making_count": loss_making,
        "pe_outlier_excluded_from_stats": pe_outlier,
        "stats": stats,
        "stats_available": len(cleaned) >= 5 and bool(stats),
        "percentile_direction": {
            "PE": "分位越高=相对行业越贵",
            "PB": "分位越高=相对行业越贵",
            "ROE": "分位越高=相对行业盈利能力越强",
            "market_cap": "仅列表展示，不进分位统计",
        },
    }


def _fetch_board_constituents_akshare(board_name: str) -> list[dict[str, Any]]:
    """akshare 行业成分兜底（无实时 PE 时需再拼；优先用带指标的 push2）。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    try:
        df = ak.stock_board_industry_cons_em(symbol=board_name)
    except Exception as exc:
        logger.warning("akshare board cons fail: %s", exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    out: list[dict[str, Any]] = []
    # 列名随 ak 版本可能变化
    cols = {str(c): c for c in df.columns}
    code_col = next((cols[k] for k in cols if "代码" in k), None)
    name_col = next((cols[k] for k in cols if "名称" in k), None)
    pe_col = next((cols[k] for k in cols if "市盈率" in k), None)
    pb_col = next((cols[k] for k in cols if "市净率" in k), None)
    roe_col = next((cols[k] for k in cols if "ROE" in k.upper() or "净资产收益率" in k), None)
    mc_col = next((cols[k] for k in cols if "总市值" in k), None)
    if code_col is None:
        return []
    for _, row in df.iterrows():
        bare = str(row[code_col]).zfill(6)
        name = str(row[name_col]) if name_col else ""
        mc = to_float(row[mc_col]) if mc_col else None
        # ak 总市值常为亿
        out.append({
            "code": _suffix_code(bare),
            "name": name,
            "pe": to_float(row[pe_col]) if pe_col else None,
            "pb": to_float(row[pb_col]) if pb_col else None,
            "roe": to_float(row[roe_col]) if roe_col else None,
            "market_cap": round(mc, 4) if mc is not None else None,
        })
    return out


def _fetch_constituents_with_fallback(board_code: str, board_name: str) -> tuple[list[dict[str, Any]], str]:
    """返回 (peers, source_tag)。"""
    try:
        peers = _fetch_board_constituents(board_code)
        if peers:
            return peers, "push2"
    except Exception as exc:
        logger.warning("push2 constituents fail: %s", exc)
    ak_peers = _fetch_board_constituents_akshare(board_name)
    if ak_peers:
        return ak_peers, "akshare"
    raise RuntimeError(f"无法获取板块 {board_code}({board_name}) 成分股")


class ScreenPeersTool(BaseTool):
    name = "screen_peers"
    summary = "同业估值对比（东财行业板 PE/PB/ROE/市值）"
    description = (
        "按东财行业板块拉成分股，输出可比公司表 + PE/PB/ROE 行业分位。"
        "默认排除 ST；PE<0 或 >200 不计入分位。不输出贵/便宜解读。"
        "可传 board_code 显式指定板块。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "目标 A 股，如 600519.SH"},
            "board_code": {
                "type": "string",
                "description": "可选，东财板块代码如 BK1575，显式指定可比板",
            },
            "exclude_st": {"type": "boolean", "default": True},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = validate_a_share(str(args.get("code") or ""))
        if code is None:
            return err("需要有效的 A 股代码 .SH/.SZ/.BJ")
        board_code = (args.get("board_code") or "").strip() or None
        exclude_st = args.get("exclude_st", True)
        if isinstance(exclude_st, str):
            exclude_st = exclude_st.lower() not in ("0", "false", "no")

        quality = "normal"
        note_parts: list[str] = []

        try:
            membership = _fetch_membership(code)
            if not membership and not board_code:
                return err(f"未找到 {code} 的板块归属")

            industry_meta = _fetch_industry_board_meta()
            chosen = select_industry_board(
                membership, industry_meta, board_code=board_code
            )
        except Exception as exc:
            return err(f"板块解析失败: {exc}")

        try:
            peers, src = _fetch_constituents_with_fallback(
                chosen["board_code"], chosen.get("board_name") or chosen["board_code"]
            )
            if src != "push2":
                quality = "degraded"
                note_parts.append(f"成分来自 {src} 兜底")
        except Exception as exc:
            logger.warning("all constituent sources fail, PE top500 degraded: %s", exc)
            quality = "degraded"
            note_parts.append(
                "板块成分取自 PE 排序 top500 子集，小盘/亏损股可能遗漏"
            )
            try:
                peers = _degraded_universe()
            except Exception as exc2:
                return err(f"成分股获取失败: {exc}; 回退亦失败: {exc2}")

        codes = {p["code"].upper() for p in peers}
        if code.upper() not in codes:
            note_parts.append("目标股不在当前成分/子集中，分位仅供参考")

        built = build_peer_stats(peers, code, exclude_st=bool(exclude_st))
        table = sorted(
            built["peers"],
            key=lambda r: r.get("market_cap") or 0,
            reverse=True,
        )

        data = {
            "code": code,
            "board_code": chosen["board_code"],
            "board_name": chosen.get("board_name"),
            "board_selection": chosen.get("selection"),
            "board_candidates": chosen.get("candidates"),
            "constituent_count": built["constituent_count"],
            "st_removed": built["st_removed"],
            "loss_making_count": built["loss_making_count"],
            "pe_outlier_excluded_from_stats": built["pe_outlier_excluded_from_stats"],
            "target": built["target"],
            "peers": table,
            "stats": built["stats"],
            "stats_available": built["stats_available"],
            "percentile_direction": built["percentile_direction"],
            "note": (
                "成分股少于5只，仅输出列表、不分位"
                if built["constituent_count"] < 5
                else None
            ),
        }
        return ok(
            data,
            quality=quality,  # type: ignore[arg-type]
            note="; ".join(note_parts) if note_parts else None,
            market="a_share",
            source="eastmoney",
            tool="screen_peers",
        )
