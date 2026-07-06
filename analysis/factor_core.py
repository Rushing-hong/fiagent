"""因子分析核心：IC/IR + 分层回测。"""

from __future__ import annotations

import pandas as pd

_MIN_VALID_PER_DATE = 5


def compute_ic_series(factor_df: pd.DataFrame, return_df: pd.DataFrame) -> pd.Series:
    common_dates = factor_df.index.intersection(return_df.index)
    common_codes = factor_df.columns.intersection(return_df.columns)
    if len(common_dates) == 0 or len(common_codes) == 0:
        return pd.Series(dtype=float)

    factor_df = factor_df.loc[common_dates, common_codes]
    return_df = return_df.loc[common_dates, common_codes]
    pair_mask = factor_df.notna() & return_df.notna()
    n_valid = pair_mask.sum(axis=1)
    factor_aligned = factor_df.where(pair_mask)
    return_aligned = return_df.where(pair_mask)
    factor_ranks = factor_aligned.rank(axis=1, method="average")
    return_ranks = return_aligned.rank(axis=1, method="average")
    ic = factor_ranks.corrwith(return_ranks, axis=1, method="pearson")
    ic = ic[n_valid >= _MIN_VALID_PER_DATE].dropna()
    return ic.astype(float) if not ic.empty else pd.Series(dtype=float)


def compute_group_equity(
    factor_df: pd.DataFrame, return_df: pd.DataFrame, n_groups: int,
) -> pd.DataFrame:
    common_dates = sorted(factor_df.index.intersection(return_df.index))
    common_codes = factor_df.columns.intersection(return_df.columns)
    if not common_dates or len(common_codes) == 0:
        return pd.DataFrame()

    factor_df = factor_df.loc[common_dates, common_codes]
    return_df = return_df.loc[common_dates, common_codes]
    group_returns: dict[str, list[float]] = {f"Group_{i+1}": [] for i in range(n_groups)}
    valid_dates = []

    for date in common_dates:
        f = factor_df.loc[date].dropna()
        r = return_df.loc[date].dropna()
        shared = f.index.intersection(r.index)
        if len(shared) < n_groups:
            continue
        valid_dates.append(date)
        ranked = f[shared].rank(method="first")
        bins = pd.qcut(ranked, n_groups, labels=False, duplicates="drop")
        if bins.nunique() < n_groups:
            bins = pd.cut(ranked, n_groups, labels=False)
        for g in range(n_groups):
            members = bins[bins == g].index
            group_returns[f"Group_{g+1}"].append(
                float(r[members].mean()) if len(members) else 0.0
            )

    if not valid_dates:
        return pd.DataFrame()
    ret_df = pd.DataFrame(group_returns, index=valid_dates)
    return (1 + ret_df).cumprod()
