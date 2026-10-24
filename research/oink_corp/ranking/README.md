# OINK CORP ranking research

Offline, development-only comparisons of explicitly specified ranking
candidates (see `apps/api/app/research/ranking.py`).

- `candidates/` — ranking models (`linear_weighted_v1`): direction, features,
  cross-sectional transforms and **manually chosen** weights.
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
