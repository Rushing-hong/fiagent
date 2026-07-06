"""Options market tool: T-quote with Greeks and PCR.

Supports Shanghai/Shenzhen ETF options: 50ETF, 300ETF, 500ETF, 1000ETF,
STAR 50 ETF (科创50), ChiNext ETF (创业板).
"""

from __future__ import annotations

from typing import Any

from market.akshare_data import get_option_chain
from tools.base import BaseTool


class OptionChainTool(BaseTool):
    name = "get_option_chain"
    summary = "ETF期权T型报价（含隐含波动率、希腊字母、PCR）"
    description = (
        "获取 ETF 期权 T 型报价表，包含认购/认沽期权的价格、持仓量、成交量、"
        "隐含波动率(IV)、Delta/Gamma/Theta/Vega/Rho 希腊字母。"
        "同时返回 Put-Call Ratio (PCR)。\n"
        "支持标的: 50ETF(510050), 300ETF沪(510300), 300ETF深(159919), "
        "500ETF(510500), 1000ETF(512100), 科创50ETF(588000), 创业板ETF(159915)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "underlying": {
                "type": "string",
                "enum": ["50ETF", "300ETF_SH", "300ETF_SZ", "500ETF", "1000ETF", "KCB50ETF", "CYBETF"],
                "default": "50ETF",
                "description": "标的 ETF",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        return get_option_chain(underlying=str(args.get("underlying", "50ETF")))
