# 🧬 OINK CORP

OINK CORP is the research laboratory for Market Truffler's trading strategy.

It is a **repository boundary, not a running service**. Nothing in this
directory is part of the production runtime (`docker-compose.yml` does not
build or start anything from here).

## Conceptual role

OINK CORP develops and validates the strategy; Market Truffler (Sniffer →
Truffler → Warhog) runs it. Concretely, this is where future work will live
for things like:

- quantitative feature research
- backtesting
- Martin Gale simulation and calibration
- ablation studies
- walk-forward validation

See [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) for how this relates
to the production domains.

## Status

Not implemented in the foundation milestone. This directory exists to reserve
the boundary; no research infrastructure, notebooks, or dependencies have
been added yet.
