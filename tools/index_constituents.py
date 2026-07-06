"""Index constituent tool: CSI 300/500/1000/A500/STAR 50 components with weights."""

from __future__ import annotations

from typing import Any

from market.envelope import err, ok, to_float
from tools.base import BaseTool

_INDEX_MAP = {
    "csi300":  "沪深300",
    "csi500":  "中证500",
    "csi1000": "中证1000",
    "a500":    "中证A500",
    "star50":  "科创50",
    "chinext": "创业板指",
    "sse50":   "上证50",
    "bse50":   "北证50",
}


class IndexConstituentsTool(BaseTool):
    name = "get_index_constituents"
    summary = "指数成分股及权重（沪深300/A500/科创50等）"
    description = (
        "获取主流 A 股指数的成分股列表和权重。\n"
        f"支持: {', '.join(_INDEX_MAP.values())}。\n"
        "用于指数增强策略、指数调仓事件驱动、行业偏离分析。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "index": {
                "type": "string",
                "enum": list(_INDEX_MAP.keys()),
                "default": "csi300",
                "description": "指数代码",
            },
        },
        "required": ["index"],
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        try:
            import akshare as ak
        except ImportError:
            return err("akshare 未安装。请执行: pip install akshare")

        index = str(args.get("index", "csi300"))
        try:
            symbol_map = {
                "csi300": "000300", "csi500": "000905", "csi1000": "000852",
                "a500": "000510", "star50": "000688", "chinext": "399006",
                "sse50": "000016", "bse50": "899050",
            }
            sym = symbol_map.get(index, "000300")
            df = ak.index_stock_cons(symbol=sym)
            if df is None or df.empty:
                return err(f"未获取到 {_INDEX_MAP.get(index, index)} 成分股数据")
        except Exception as e:
            return err(f"成分股数据获取失败: {e}")

        records = []
        weight_col = None
        for col in df.columns:
            if "权重" in str(col) or "weight" in str(col).lower():
                weight_col = col
                break

        for _, row in df.iterrows():
            records.append({
                "code": str(row.get("品种代码", row.get("code", ""))),
                "name": str(row.get("品种名称", row.get("name", ""))),
                "weight": to_float(row.get(weight_col)) if weight_col else None,
            })

        return ok({
            "index": _INDEX_MAP.get(index, index),
            "index_code": sym,
            "count": len(records),
            "constituents": records,
        }, source="akshare", market="a_share")
