"""Unit tests for thin DCF calculator."""

from __future__ import annotations

import pytest

from tools.calc_dcf import compute_dcf


def _base_kwargs(**over):
    kw = dict(
        revenue_t0=100.0,
        revenue_growth=[0.10, 0.10, 0.08, 0.06, 0.05],
        ebit_margin=0.20,
        tax_rate=0.25,
        da_to_revenue=0.05,
        capex_to_revenue=0.06,
        nwc_to_delta_revenue=0.10,
        rf=0.025,
        beta=1.0,
        erp=0.06,
        debt_weight=0.2,
        kd=0.04,
        g=0.025,
        shares=10.0,
        net_debt=0.0,
        exit_multiple=12.0,
    )
    kw.update(over)
    return kw


def test_dcf_rejects_g_ge_wacc():
    with pytest.raises(ValueError, match="永续增长率必须小于 WACC"):
        compute_dcf(**_base_kwargs(g=0.20, rf=0.02, beta=1.0, erp=0.05))


def test_dcf_gordon_identity_and_sensitivity_shape():
    r = compute_dcf(**_base_kwargs())
    assert r["wacc"] > 0
    assert len(r["years"]) == 5
    assert r["gordon"]["per_share"] > 0
    assert r["exit"] is not None
    assert len(r["sensitivity"]["wacc_cols"]) == 5
    assert len(r["sensitivity"]["g_rows"]) == 5
    assert len(r["sensitivity"]["per_share"]) == 5
    assert len(r["sensitivity"]["per_share"][0]) == 5
    # 负 FCFF 不跳过：把 capex 拉高
    r2 = compute_dcf(**_base_kwargs(capex_to_revenue=0.5, ebit_margin=0.05))
    assert any(y["fcff"] < 0 for y in r2["years"])
    assert r2["gordon"]["enterprise_value"] == r2["gordon"]["enterprise_value"]  # not nan skip


def test_dcf_warns_high_g_but_runs():
    r = compute_dcf(**_base_kwargs(g=0.06, rf=0.03, beta=1.2, erp=0.07, debt_weight=0.1, kd=0.05))
    assert any("5%" in w for w in r["warnings"])
    assert r["gordon"]["per_share"] > 0
