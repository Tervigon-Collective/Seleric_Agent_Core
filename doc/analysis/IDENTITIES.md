# Analysis primitives: mathematical identities (DRAFT for sign-off)

Status: **DRAFT. No code in `app/analysis.py` is written until this is reviewed and signed off.**

Purpose: define, before any implementation, what each primitive means, when it
may run, and the identity its output must satisfy. Implementations are
certified against this document (property tests plus independent SQL), **not**
against the legacy Agent code. Where legacy disagrees with this document,
legacy is the defect.

## 0. Conventions (apply to every primitive)

- **Windows.** A comparison has a *current* window `C` and a *baseline* window `B`.
  `delta = value(C) - value(B)`. `pct_change = delta / value(B)` and is `null`
  when `value(B) == 0` or `null`.
- **Equal-length rule.** Totals of *additive* metrics over windows of different
  length are not comparable. The primitive refuses (`refusal: unequal_windows`)
  unless the caller passes `normalize="per_day"`, in which case values are
  divided by the number of days and the output says so.
- **Null is not zero.** A missing observation stays `null`. A segment present in
  one window and absent in the other is `status: new | disappeared`, with the
  missing side `null` for display and `0` only inside the arithmetic of an
  *additive* metric (stated per primitive below).
- **Residual.** Every decomposition returns `residual = delta - sum(parts)` and
  `identity_verified = abs(residual) <= tol`, with `tol = 1e-9 * max(1, abs(delta))`
  for exact identities. A non-exact method must say so and report its residual.
- **Metric kinds** (from the catalogue):
  - *additive*: sums across rows, segments and days (revenue, orders, spend).
  - *ratio*: `numerator / denominator * multiplier`; `ratio_components` give the
    parts. A ratio is **never** summed or averaged; its total over any set of
    rows is `sum(numerator) / sum(denominator) * multiplier` (existing
    `insight_engine.metric_total`).
  - *non-decomposable* (e.g. distinct counts, medians): totals are not derivable
    from segments; segment primitives refuse (`refusal: not_decomposable`).
- **Grain compatibility.** All inputs to one primitive share the same
  dimensions grain and unit; otherwise refuse (`refusal: grain_mismatch`).
- **Scope.** Every primitive runs on a stored `query_id` and inherits its
  brand/module scope. It can narrow but never widen it.

## 1. compare

Inputs: one metric, windows `C` and `B`.

| Output | Definition |
|---|---|
| `value_C`, `value_B` | `metric_total` over each window (ratio recomputed from components). |
| `delta`, `pct_change` | per section 0. |

Preconditions: equal-length rule; metric not `non-decomposable` only if the
value must be derived from rows (a directly queried total is fine).
Identity: `delta == value_C - value_B` exactly.

## 2. contribution (additive metrics, by one dimension)

Inputs: additive metric `m`, dimension `d`, windows `C`, `B`.

For segment `s`: `delta_s = m_C(s) - m_B(s)` where an absent side is `0`.

Output per segment: `delta_s`, `share_of_delta_s = delta_s / delta` (`null` when
`delta == 0`), `status` in `{continuing, new, disappeared}`.

Identity: `sum_s delta_s == delta` where `delta` is the metric's total delta
computed **independently** (not as the sum of the segments).
If rows were truncated (`limit`), the remainder is reported as
`other = delta - sum(delta_s)` and `identity_verified = false` unless
`other` is explicitly computed from an unsliced total query.

Notes: shares may exceed 100% or be negative when segments move in opposite
directions; this is reported as is, never clipped.

## 3. mix_shift (ratio metric `R = N / D`, by one dimension)

Inputs: ratio metric with components `N`, `D`; dimension `d`; windows `C`, `B`.

Segment weight `w_s(P) = D_s(P) / D(P)` and rate `r_s(P) = N_s(P) / D_s(P)`
for `P in {C, B}`. Overall `R(P) = sum_s w_s(P) * r_s(P)`.

Decomposition of `R(C) - R(B)` per segment, with `Δw = w_s(C) - w_s(B)`,
`Δr = r_s(C) - r_s(B)`:

- `rate_effect_s = w_s(B) * Δr`
- `mix_effect_s  = Δw * r_s(B)`
- `interaction_s = Δw * Δr`

Identity (exact): `sum_s (rate_effect_s + mix_effect_s + interaction_s) == R(C) - R(B)`.

Preconditions: both components additive and present for every segment;
`D_s > 0` in the windows where `r_s` is used. A segment with `D_s(P) == 0` has
`r_s(P)` undefined: its contribution is attributed to `mix_effect` using
`r_s = R(P)` of the *other* window and flagged `imputed_rate`. (Open question Q2.)
The primitive first verifies `R(P) == sum w*r` on the data; if it fails, it
refuses (`refusal: weighted_identity_failed`).

## 4. funnel (ordered stages `S_1 ... S_k`, counts)

Inputs: ordered stage counts `n_1 ... n_k` per window, same population definition.

Stage conversion `c_i = n_{i+1} / n_i`. Overall `n_k / n_1 = prod_i c_i`.

Output per window: `c_i`, cumulative `n_i / n_1`, and for the comparison the
log-share of each stage in the overall change:

`ln(n_k/n_1)|_C - ln(n_k/n_1)|_B = sum_i [ln c_i|_C - ln c_i|_B]`

Identity (exact): the stage log-changes sum to the overall log-change.
Preconditions: `n_i > 0` for all stages in both windows; stages are nested
(`n_{i+1} <= n_i`) or the output flags `non_nested` (e.g. events counted at a
different grain than users). Missing stage count -> `null`, never `0`, and the
stages around it are not chained. The ordering comes from the catalogue funnel
definition, never inferred from the rates.

## 5. rank

Inputs: metric, dimension, optional `top_n`.
Output: ordered segments (nulls last, ties broken by segment key) plus
`total` from an **unsliced** query and `coverage = sum(shown) / total` for
additive metrics.
Identity (additive): `sum(shown) + other == total`.
Ratio metrics: no coverage identity; the total is the recomputed ratio.

## 6. decompose (multiplicative chain `Y = k * prod_i X_i`)

Inputs: a verified identity from `formula.depends_on` / `ratio_components`
(for example revenue = sessions * conversion * AOV).

Method: Shapley attribution of `Y(C) - Y(B)` over the factors `X_i`
(each factor is switched from its `B` value to its `C` value; the contribution
is the average marginal effect over all orderings).

Identity (exact, "efficiency"): `sum_i phi_i == Y(C) - Y(B)`.
Preconditions: the product identity `Y == k * prod X_i` holds on **both**
windows within `tol` (verified from the data, not assumed from the formula);
else refuse (`refusal: identity_not_verified`). At most 6 factors (720 orderings).
Shapley is a *fair attribution convention*, not a causal claim; the output
labels it `kind: arithmetic_attribution`.

## 7. anomalies

Inputs: daily series of one metric, reference rule.
Reference (default): same weekday, previous `W` weeks (default `W = 4`),
excluding the target day. Score `z = (x - median) / (1.4826 * MAD)`.
Flag when `|z| >= 3.5` **and** `MAD > 0` **and** at least `W - 1` reference points exist.
Also keep a separate `zero_run` flag (>= 2 consecutive zero days for a metric
that is positive in the reference).
No identity; certified by fixtures (known outliers, constant series, short
history -> `insufficient_history`). This is a *screen*, not evidence of cause.

## 8. What `explained_share` means

Defined only for sections 2, 3 and 6 when `identity_verified` is true:
`explained_share = sum(|parts in the selected top-K|) / sum(|all parts|)`
(absolute, so offsetting movements are not hidden). It measures concentration
of the movement across *compatible* parts, not confidence in any explanation,
and is `null` for every other primitive.

## 9. Certification

1. Property tests (`hypothesis`) generate valid random inputs and assert each
   identity, and generate invalid inputs and assert the named refusal.
2. Fixtures with hand-computed values taken from this document.
3. Live: each primitive on brand 20 vs independent ClickHouse SQL.
4. Legacy comparison is diagnostic only; every divergence is triaged as
   legacy-defect or new-defect.

## Open questions for sign-off

- **Q1 (sign convention).** `delta = current - baseline` everywhere. Legacy
  `period_deltas` used A-B in question order. Confirm current - baseline.
- **Q2 (zero-denominator segments in mix_shift).** Impute the other window's
  overall rate and flag, or refuse the whole decomposition?
- **Q3 (unequal windows).** Refuse by default and allow `per_day`
  normalization only on request, or auto-normalize with disclosure?
- **Q4 (funnel non-nested stages).** Refuse, or compute with the `non_nested` flag?
- **Q5 (anomaly thresholds).** `|z| >= 3.5`, `W = 4` weeks, `MAD > 0`: acceptable defaults for this business's seasonality (sales, festival weeks)?
- **Q6 (Shapley vs log decomposition).** Shapley is order-free and exact but
  costs `k!`; log-decomposition is exact for pure products. Keep Shapley for
  generality?
- **Q7 (polarity).** Which metrics are "bad when up" (`direction_bad`)? Needs a
  business-owner review of the 233 catalogue metrics; the Agent's
  `metric_registry.yaml` has 74 entries to start from.
