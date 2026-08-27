# Flagship Theory: Persistent Interface Risk and Governance Liquidity

## B.1 Canonical local allocation model

Consider unit exposures with declared value `v`, group `G in {A,B}`, and a fixed
capacity allocation. At a binding cap, let `M` be the exposure removed from the
selected A group. Order the displaced A exposures from the selection boundary
inward and the feasible B replacements from the boundary outward. Their value
quantiles are `q_-(u)` and `q_+(u)`, respectively, for `u in [0,M]`. The exact
linear objective cost is

`C(M) = integral_0^M [q_-(u)-q_+(u)] du.`

Assume the two boundary quantiles are differentiable at zero, with positive
dollar-weighted densities `f_-(t_-)` and `f_+(t_+)` at their respective boundary
values. Then

`g(u)=q_-(u)-q_+(u) = g(0) + kappa u + o(u),`

where

`kappa = 1/f_-(t_-) + 1/f_+(t_+).`

Hence

`C(M) = g(0)M + (kappa/2)M^2 + o(M^2).`

Define local governance liquidity as `L_G = 1/kappa`. High `L_G` means dense
near-boundary substitutes and a slowly rising marginal cap cost. This is a local
property of the declared objective and dollar-weighted candidate pool, not a
generic market-liquidity measure.

## B.2 Theorem PT1: Persistent noncomposition by replication

Let `Gamma_n` be a component certificate containing group-blind predictive
performance, calibration, policy compliance, aggregate group margins, and
outcome-monitor accuracy, but no group-conditioned boundary matching. Let
`K(Gamma_n)` be the set of cap costs compatible with that certificate.

**Theorem PT1.** There is a sequence of replicated boundary economies for which
model-estimation error, calibration error, cap-compliance error, and
outcome-monitor error are zero for every `n`, while

`liminf_n diam K(Gamma_n) >= delta > 0.`

**Construction and proof.** In each block, select three of four unit exposures
with declared values `(10,9,8,1)` and a cap allowing at most one A exposure.
World P assigns A to values 10 and 9; World Q assigns A to values 9 and 8. Both
worlds have the same group margin (two A exposures), the same score-outcome law
when the score equals the deterministic outcome, the same unconstrained A share
(two of three), the same constrained A share (one of three), and one unit of
exchanged mass. Their costs are respectively 8 and 7: P replaces value 9 with 1,
whereas Q replaces value 8 with 1. Replicate each block `n` times under the same
blockwise allocation policy and normalize by the number of blocks. All listed
component errors remain zero and the compatible normalized costs differ by one.
Thus the identification-set diameter is at least one for every `n`. QED.

The theorem explicitly fixes the information regime. A certificate that releases
the full exposure-level group-score-action record can reconstruct the interface
and is outside the claim.

## B.3 Conjecture PT2: Accuracy-governance cost reversal

For two group-blind probabilistic scores `s_1` and `s_2` on a fixed candidate
pool, define `C(s,c)` as declared cap cost under the same cap and allocation
rule.

**Conjecture PT2.** There exist environments and scores such that

`AUC(s_2) > AUC(s_1), Brier(s_2) < Brier(s_1), LogLoss(s_2) < LogLoss(s_1),`

while `C(s_2,c) > C(s_1,c)`.

The mechanism is selection reallocation. A better score can place more high-value
A exposures above the unconstrained boundary. A later A cap then removes more
valuable exposure or confronts a steeper replacement schedule. This is not a
claim that better prediction is undesirable; it says model improvement and the
price of a pre-existing governance constraint have no universal ordering.

## B.4 Integrated proposition to pursue

The three objects form one chain:

`prediction refinement -> boundary composition -> (g(0), L_G) -> cap cost.`

PT1 states why ordinary component improvement does not reveal this chain. PT2
states that a refinement can steepen it. The local law states which observable
boundary quantities determine the small-cap-cost response. A complete theory
would prove PT1 and PT2, estimate `g(0)` and `L_G` in the seven-domain data, and
compare the marginal governance benefit of improved substitution supply with the
benefit of improved prediction.

## B.5 Claims deliberately excluded

The current public data cannot identify the causal effect of expanding a lender's
candidate supply, realized institutional profit, or the welfare effect of a cap.
The liquidity object therefore measures score-implied local substitutability. Any
claim that sourcing investment dominates model investment requires either an
institutional field setting or a clearly labeled structural simulation.

## B.6 Proposed manuscript insertion: managerial implication

The results suggest a different allocation question once a prediction system is
already mature. Further improvement in average predictive accuracy need not make
a binding portfolio policy easier to execute. A refinement can alter who reaches
the active boundary and thereby increase the value displaced by a later cap. By
contrast, a deeper pool of near-boundary substitutes flattens the local exchange
curve. This points to a testable managerial hypothesis: after basic predictive
quality is achieved, investments that expand qualified substitute supply--for
example, sourcing breadth, approval timing flexibility, or a retained near-tie
candidate pool--may reduce the score-implied cost of governance more than another
incremental increase in AUC.

The paper does not estimate that causal comparison. Public credit data identify
local score-implied substitutability, not the return to a lender's sourcing
investment. The contribution is to make the trade-off visible and measurable:
model refinement changes boundary composition, whereas governance liquidity
changes the cost of replacing boundary exposures. A field study with lender-level
candidate pools and sourcing interventions is needed to determine which lever has
the larger institutional return.
