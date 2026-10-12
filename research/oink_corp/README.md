# 🧬 OINK CORP

OINK CORP is Market Truffler's intelligence: *Sniffer senses, OINK CORP
thinks, Warhog acts.* It owns research (offline) and, eventually, the live
Long/Short rankings that produce 🍄 Truffles — two operationally isolated
halves. This directory is the research half's home for notebooks and
generated data.

It is a **repository boundary, not a running service**. `docker-compose.yml`
does not build or start anything from here — the `api` service only
bind-mounts `data/` (below) as a place to write generated output, the same
direction as a log file, never the other way around.

## Conceptual role

Research investigates which market characteristics identify attractive
Long and Short opportunities — starting from human **heuristics** (natural-
language intuitions, which research may reject) and, eventually, from its
own hypotheses, both judged by the same evaluation standard. Validated
results become Challenger ranking models; a human-approved Champion runs in
the deterministic live ranking runtime, which never depends on research.
Future work here includes hypothesis experiments, chronological and
regime-aware evaluation with protected holdout data, path-dependent outcome
labels for the Martingale-inspired strategy, and Martingale simulation.

See [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) for how this relates
to the production domains.

## Status

The first real capability now exists: **historical Sniff replay + forward-
outcome research datasets**. `apps/api/app/scripts/generate_research_dataset.py`
replays what today's production `FeatureEngine` would measure at a historical
`frame_time` (reusing the real feature code, never a research copy), attaches
research-only `forward_return_H`/`forward_relative_return_H` labels, and
writes the result as a Parquet file + `manifest.json` under `data/`
(gitignored — regenerable, never committed). The actual code lives in
`apps/api/app/research/` (tested/typed alongside the rest of the backend,
imported by nothing production) rather than in this directory, since this
boundary is documentation/notebook territory, not a Python package — see
`docs/ARCHITECTURE.md`'s OINK CORP section for the full data flow and
temporal-safety invariants.

Research rules on the 5m base timeframe:

- Sampled `frame_time`s are always 5-minute boundaries;
  `--sample-interval-minutes` must be a multiple of 5.
- `forward_return_H = P(T+H) / P(T) - 1`, where `P(X)` is the close of the
  5m candle closing exactly at `X`. `H` must be a multiple of 5 minutes;
  anything else is rejected, never rounded. A missing candle at either end
  gives null.
- Datasets are versioned (`dataset_schema_version` 2, `base_timeframe`
  `5m`, in both `manifest.json` and the Parquet metadata).
  `app.research.dataset.read_dataset` refuses anything else, including
  every dataset generated before the 5m migration. Regenerate old datasets
  rather than mixing them.

Still not implemented: heuristics, hypotheses, experiments, evaluation,
ranking models, Champion/Challenger promotion, Score, Rank, Truffles,
backtesting, or Martingale simulation. No notebooks exist yet — none should
be added merely to look implemented.
