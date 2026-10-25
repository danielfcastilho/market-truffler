# OINK CORP ranking research

Offline, development-only comparisons of explicitly specified ranking
candidates (see `apps/api/app/research/ranking.py`).

- `candidates/` — ranking models (`linear_weighted_v1`): direction, features,
  cross-sectional transforms and **manually chosen** weights.
- `qualifications/` — experimental Truffle qualification specs used by the
  live Sniffer rankings (not research results; see
  `docs/TRUFFLE_QUALIFICATION.md`). Each names the candidate it applies to.
- `studies/` — registered comparisons: candidate, reference, datasets, label,
  top K. The primary metric is fixed by the ranking evaluator (top-K excess
  utility; paired difference against the reference).

Run: `python -m app.scripts.run_ranking_study --study ranking/studies/<id>.json`
(from `apps/api`, with `--data-dir`). Results are immutable runs under
`data/runs/<study_id>/`.

The first candidates mirror the pre-registered Price × Open Interest campaign
(H1, H3): price reversal, plus falling Open Interest at half the weight of
price. The weights are illustrative and fixed — nothing was tuned, and the
campaign's 2026-10-12 development results (which showed continuation, not
reversal) were known when they were written down; they were not used to
choose them. No candidate is validated, none is a Champion, none is a
trading signal.

## Pre-registered: studies on reconstructed data (datasets pending)

Recorded 2026-10-25 21:30 UTC, before any ranking statistic was computed on
reconstructed data. The only reconstructed outcome values read so far were
label parity checks against live-observed rows (3 timestamps of 2026-09-28,
4 of 2026-10-12): equality checks, no performance figure.

- **Data:** every reconstructed daily dataset `recon_s15m_YYYYMMDD` for
  2026-09-28 … 2026-10-24 (27 days) that the running reconstruction publishes
  and that passes `generate_research_dataset --verify`. A day that fails is
  excluded and recorded as such, never replaced or re-selected. The studies
  run once, after the last day is published — not on a subset, not repeatedly
  as days arrive. Reconstructed data only (never pooled with live-observed
  shards — the loader refuses that).
- **Studies** (ranking evaluator v1, development segment only; the latest
  max(3, N/3) qualifying days stay untouched as the evaluator's validation
  segment), each `forward_relative_return_1h`, `top_k` 20:
  1. `long-oi-vs-price-recon-v1`: `long-price-reversal-oi-v1` vs reference
     `long-price-reversal-v1` (same design as `long-oi-vs-price-v1`).
  2. `short-oi-vs-price-recon-v1`: `short-price-reversal-oi-v1` vs
     `short-price-reversal-v1`.
  3. `long-live-vs-market-recon-v1`: `long-price-reversal-oi-v1`, no reference
     (top 20 vs the eligible universe).
  4. `short-live-vs-market-recon-v1`: `short-price-reversal-oi-v1`, no reference.
- **Multiplicity:** four exploratory primary tests at the evaluator's 90%
  level, uncorrected. Any directional verdict is exploratory development
  evidence on reconstructed (potentially survivorship-biased) data, and no
  model's status changes because of it: only independent prospective
  evidence does that.
- **Unchanged:** models, weights, thresholds, labels and the evaluator. No
  candidate is tuned on these results.
