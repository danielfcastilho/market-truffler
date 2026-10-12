"""🧬 OINK CORP — Research (offline).

Historical, point-in-time research over what Sniffer observed:

- `replay`: reconstructs historical Sniffs by running Sniffer's own
  `FeatureEngine` against a historical frame — never a copy of the
  feature math.
- `forward_returns`: future-outcome labels — the only code allowed to read
  data after a frame's `frame_time`.
- `dataset`: assembles the two into versioned Parquet research datasets.

Contract: this package may import Sniffer's code; nothing in the live
pipeline (features, services, routers, the app itself) may import this
package (enforced by `tests/test_architecture_boundaries.py`), so research
labels can never leak into live observations. This is research only — not
OINK CORP's future live ranking runtime, which must be deterministic and
must not depend on experiments (see docs/ARCHITECTURE.md).
"""
