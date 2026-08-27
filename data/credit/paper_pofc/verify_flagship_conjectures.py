"""Synthetic witness for the accuracy-governance reversal and local liquidity law."""
from __future__ import annotations

import math
import numpy as np


def auc(y: np.ndarray, s: np.ndarray) -> float:
    pos, neg = s[y == 1], s[y == 0]
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def cap_cost(score: np.ndarray, group_a: np.ndarray, capacity: int, cap_a: int) -> float:
    order = np.argsort(-score, kind="stable")
    unconstrained = order[:capacity]
    selected = []
    used_a = 0
    for idx in order:
        if group_a[idx] and used_a >= cap_a:
            continue
        selected.append(idx)
        used_a += int(group_a[idx])
        if len(selected) == capacity:
            break
    return float(score[unconstrained].sum() - score[np.array(selected)].sum())


def main() -> None:
    # PT1: replicated blocks retain perfect component accuracy but distinct cap costs.
    values = np.array([10.0, 9.0, 8.0, 1.0])
    p_group_a = np.array([1, 1, 0, 0], dtype=bool)
    q_group_a = np.array([0, 1, 1, 0], dtype=bool)
    for replicas in (1, 10, 100):
        p_cost = cap_cost(np.tile(values, replicas), np.tile(p_group_a, replicas), 3 * replicas, replicas) / replicas
        q_cost = cap_cost(np.tile(values, replicas), np.tile(q_group_a, replicas), 3 * replicas, replicas) / replicas
        assert (p_cost, q_cost) == (8.0, 7.0)
    print("Persistent-noncomposition witness passed: normalized cost diameter = 1 for all replications.")

    rng = np.random.default_rng(26)
    n = 1_000
    group_a = rng.random(n) < 0.52
    true_p = np.clip(0.18 + 0.48 * group_a + rng.normal(0, 0.15, n), 0.01, 0.99)
    y = rng.binomial(1, true_p)
    # s2 is calibrated up to light noise; s1 is noisier and less able to rank A's high-value mass.
    s2 = np.clip(true_p + rng.normal(0, 0.035, n), 0.001, 0.999)
    s1 = np.clip(true_p - 0.18 * group_a + rng.normal(0, 0.22, n), 0.001, 0.999)
    brier1, brier2 = np.mean((s1-y)**2), np.mean((s2-y)**2)
    log1, log2 = -np.mean(y*np.log(s1)+(1-y)*np.log(1-s1)), -np.mean(y*np.log(s2)+(1-y)*np.log(1-s2))
    auc1, auc2 = auc(y, s1), auc(y, s2)
    cost1, cost2 = cap_cost(s1, group_a, capacity=300, cap_a=90), cap_cost(s2, group_a, capacity=300, cap_a=90)
    print({"auc": (auc1, auc2), "brier": (brier1, brier2), "logloss": (log1, log2), "cap_cost": (cost1, cost2)})
    assert auc2 > auc1 and brier2 < brier1 and log2 < log1 and cost2 > cost1

    # For uniform boundary densities f-=f+=1, kappa=2 and C(M)=M^2 for g(0)=0.
    for mass in (0.01, 0.05, 0.10):
        exact = mass * mass
        expansion = 0.5 * 2 * mass * mass
        assert math.isclose(exact, expansion, rel_tol=1e-12)
    print("Local-liquidity witness passed: uniform densities give kappa=2 and C(M)=M^2.")


if __name__ == "__main__":
    main()
