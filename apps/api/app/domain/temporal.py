"""The temporal contract: when an input may be used for a Market Frame.

Five instants are kept distinct (see docs/TEMPORAL_CONTRACT.md):

* observation time — when the measured fact holds (an OI bucket's
  `observed_at`, a funding settlement's `funding_time`, a candle's close);
* availability time — when the fact could first be known here. For OI and
  funding this is recorded as `ingested_at`, the instant MARKET first
  stored that value (NULL on rows stored before this was recorded:
  availability unknown);
* ingestion time — `ingested_at` itself (MARKET never learns the
  exchange's own publication instant, so ingestion is the availability
  evidence, and never earlier than the truth);
* frame time — the 5-minute boundary T a frame describes;
* decision time — `decision_time(T)`, the fixed instant at which Sniffer
  measures frame T, live or in replay.

An OI or funding observation is usable for frame T only if
`observed_at <= T` and `ingested_at <= decision_time(T)` (or
`ingested_at IS NULL`, a legacy row — accepted so pre-contract data
replays exactly as before, and disclosed as availability-unverified). The
same rule runs live and in replay, so a replayed frame can only ever see
what a live analysis at its decision time could have seen. Candles are
governed by frame membership instead (closed by T, selected when the frame
finalized).
"""

from datetime import datetime, timedelta

#: Fixed delay between a frame's boundary and the instant its inputs are
#: frozen for measurement. Bybit publishes OI bucket T about 10-20s after
#: T, and the boundary-aligned reconciler rounds finish by about T+30-40s
#: (measured 2026-10-24); 60s keeps margin without making a frame's
#: measurement depend on when analysis happened to run. Part of the
#: research contract — changing it changes replayed values, so it is a
#: constant, not configuration.
DECISION_DELAY = timedelta(seconds=60)

#: Version of these semantics, recorded in dataset manifests.
TEMPORAL_CONTRACT_VERSION = 2


def decision_time(frame_time: datetime) -> datetime:
    """The instant frame `frame_time`'s inputs are frozen at."""
    return frame_time + DECISION_DELAY
