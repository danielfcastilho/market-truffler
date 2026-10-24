# OINK CORP Research Report — `oi-incremental-price-1h-v1`

**Status: insufficient data. No hypothesis has been tested with enough evidence to support or reject it.**

| | |
|---|---|
| Campaign | [`oi-incremental-price-1h-v1.json`](oi-incremental-price-1h-v1.json) (pre-registration; hash `cabb95a03cc401c8e41d44bccf0b577a91d572ebd0d3feb97690766506739456`) |
| Registered | 2026-10-24, before any outcome value of the campaign's datasets was read |
| Executed | 2026-10-24 16:49 UTC, development segment only |
| Code | `git 03537bf` + uncommitted campaign implementation; Evaluator v1, comparison protocol v1 |
| Runs | `research/oink_corp/data/runs/oi-price-1h-*` (10 immutable records, listed below) |

## Research question

Does Open Interest behaviour over the last hour provide **incremental**
information about a coin's next-hour market-relative performance, beyond
what the coin's own last-hour price move already provides?

## Motivation

Price and Open Interest move together for different reasons. A fall with
shrinking OI suggests positions being closed (deleveraging, liquidations);
a fall with growing OI, new positions opened into it. A rise without OI
growth suggests short covering; a rise with OI growth, new participation.
If those mechanisms differ in how temporary a move is, conditioning on OI
should separate future outcomes better than price alone. These are
interpretations, not assumptions.

## Hypotheses (pre-registered)

| | Selection (within each frame) | Direction | Prediction vs the price-only group |
|---|---|---|---|
| H1 | bottom 20% of `return_1h` **and** `oi_change_1h < 0` | Long | outperforms (positive spread) |
| H2 | bottom 20% of `return_1h` **and** `oi_change_1h > 0` | Long | differs — no sign assumed |
| H3 | top 20% of `return_1h` **and** `oi_change_1h < 0` | Short | underperforms (positive Short spread) |
| H4 | top 20% of `return_1h` **and** `oi_change_1h > 0` | Short | differs — no sign assumed (negative = continuation) |

## Methodology

- **Features** (Sniffer, point-in-time): `return_1h` (1h price return of the
  5m close) and `oi_change_1h` (1h Open Interest change), both over the same
  hour so they describe the same move.
- **Thresholds**, fixed before outcomes were seen: a cross-sectional quintile
  (`cs_percentile` ≤ 0.2 / ≥ 0.8), so "significant" is relative to the market
  at that moment and each tail holds ~160 of ~795 coins; the sign of OI change
  as the untuned boundary between positions closing and opening.
- **Outcome**: `forward_relative_return_1h`, the next-hour return minus that
  frame's cross-sectional median. It's market-relative, and its horizon
  matches the observation window. One horizon only, so no horizon could be
  chosen after the fact.
- **Baselines**:
  - **A (eligible universe):** the Evaluator v1 baseline, which is the
    same-timestamp mean of every eligible coin.
  - **B (price only):** the same quintile with no OI condition
    (`oi-price-1h-base-long-v1`, `oi-price-1h-base-short-v1`).
- **Primary evidence:** four incremental comparisons (comparison protocol v1,
  `app.research.comparison`), each a hypothesis versus its price-only
  baseline:
  - a per-timestamp spread between the candidate group and the price-only
    group;
  - counted only on comparable rows (every candidate feature and the label
    present);
  - each timestamp weighted equally;
  - the Evaluator's day split, purge, minimum-evidence thresholds, day-block
    bootstrap and verdict rules, reused unchanged.
- **Datasets**: the 15-minute continuity shards — the only series with
  complete 1h OI — pinned by hash:
  `shard_s15m_20261012` (`4f74a041…`), `shard_s15m_20261013` (`a69c1bef…`).
  The earlier 5-minute dataset has OI for only 39 timestamps (from Sep 27)
  and cannot be mixed with 15-minute shards.

## Data quality

| | Available | Needed |
|---|---|---|
| Qualifying days (≥ 12 timestamps with ≥ 50 coins) | **1** (2026-10-12; Oct 13 has one frame and is excluded) | ≥ 14 for a development verdict, ≥ 30 for a validation verdict |
| Timestamps with selections | 24 | ≥ 200 |
| Distinct selected coins | 507–715 per experiment | ≥ 30 (met) |
| `return_1h`, `oi_change_1h` completeness | 100% in both shards | — |
| Outcome completeness | 97.2% (Oct 12), 99.7% (Oct 13) | — |

- **Collection gaps:** 2026-10-12 covers 15:15–23:45 UTC with gaps (15:40–18:00
  and 21:45 missing). Oct 14–23 is a permanent gap. Collection resumed on
  2026-10-24 at 15:15; that day's shard does not exist yet.
- **Many coins, one sample:** ~795 coins over 24 timestamps of a single
  afternoon is one market episode, not thousands of independent observations.
  Coins co-move, and consecutive 1h outcomes overlap.
- **Unchanged Open Interest:** 2.5% of rows have an OI change of exactly 0,
  probably illiquid instruments with stale OI. They are in the price-only
  group but in neither OI-sign group. That is why H1 and H2, complementary by
  sign, can both show a small positive spread against the same reference.
  Observed on this first execution and recorded here; v1 stays as registered.

## Results (development segment, 2026-10-12 only)

Spreads are timestamp-weighted means of per-timestamp differences, in
fractions of the next-hour market-relative return (0.0001 = 0.01
percentage points). Uncertainty intervals are **suppressed** by the
Evaluator, since there are fewer than 10 qualifying days. Verdict for every
run: **`insufficient_data`** (1 qualifying day < 10; 24 timestamps < 200).

**Primary — incremental comparisons (candidate vs price-only):**

| | Candidate rows | Coins | Candidate mean | Price-only mean | Incremental spread | Verdict |
|---|---|---|---|---|---|---|
| H1 vs price-only Long | 2,138 | 560 | −0.00176 | −0.00159 | +0.00010 | insufficient_data |
| H2 vs price-only Long | 1,560 | 507 | −0.00137 | −0.00159 | +0.00002 | insufficient_data |
| H3 vs price-only Short | 1,899 | 580 | −0.00084 | −0.00088 | +0.00016 | insufficient_data |
| H4 vs price-only Short | 1,669 | 540 | −0.00030 | −0.00088 | +0.00019 | insufficient_data |

**Secondary — each experiment against the eligible universe (Evaluator v1):**

| Experiment | Selected (with outcome) | Coins | Selected mean | Universe mean | Spread | Verdict |
|---|---|---|---|---|---|---|
| Price only, Long | 3,703 | 689 | −0.00159 | −0.00014 | −0.00145 | insufficient_data |
| H1 | 2,138 | 560 | −0.00176 | −0.00014 | −0.00136 | insufficient_data |
| H2 | 1,560 | 507 | −0.00137 | −0.00014 | −0.00143 | insufficient_data |
| Price only, Short | 3,720 | 715 | −0.00088 | +0.00014 | −0.00099 | insufficient_data |
| H3 | 1,899 | 580 | −0.00084 | +0.00014 | −0.00083 | insufficient_data |
| H4 | 1,669 | 540 | −0.00030 | +0.00014 | −0.00080 | insufficient_data |

Reading these tables:
- **Means aren't comparable to spreads.** "Mean" columns pool rows, while
  spreads weight each timestamp equally, so the two can disagree in sign.
  The spread is the registered statistic.
- **Short outcomes are sign-flipped,** so a negative Short spread means the
  coins continued *up* relative to the market.
- **Every timestamp is distinct:** each experiment's 24 timestamps are all
  2026-10-12, with 113, 124, 71, 41, 69 and 54 selected rows lacking an
  outcome (excluded after selection and counted).

## Interpretation

There is nothing to interpret as evidence. One afternoon of one day cannot
distinguish an effect from that afternoon's market episode. The descriptive
numbers are recorded only so later replications can be compared with them,
not as findings:

- **On 2026-10-12, both price tails continued rather than reversed** (both
  price-only spreads are negative): coins that fell kept underperforming, and
  coins that rose kept outperforming, over the next hour.
- **The OI-conditioned groups differ from the price-only groups by about
  0.01–0.02 percentage points per hour.** That is an order of magnitude
  smaller than the price-only effect itself, and partly an arithmetic
  consequence of the unchanged-OI rows (see Data quality).

**Alternative explanations** that this sample cannot rule out:
- a single regime or news episode;
- the specific coins active that afternoon;
- stale OI for illiquid instruments;
- the partial-day coverage with its afternoon gap.

**Practical usefulness** is out of scope. Even a statistically clear
0.01-point hourly difference would be far below plausible trading costs,
which are not modelled.

## Limitations

- **Coverage:** 1 qualifying day against ≥ 14 needed. The binding constraint
  is days, not coins.
- **Cross-sectional dependence:** handled only by one spread per timestamp.
  Overlapping 1h windows at 15-minute sampling make consecutive timestamps
  dependent.
- **Regime dependence:** every observation comes from one afternoon.
- **Multiple testing:** four primary comparisons at 90% intervals. By chance
  alone, roughly one campaign in five would show a spurious directional
  result, hence the pre-registered rule: development results are at most
  *preliminary*, and only an agreeing validation verdict counts as support.
  H1/H2 (and H3/H4) share a reference group and are not independent
  confirmations.
- **Selection bias:** rows with missing outcomes (2–3%) are excluded after
  selection.
- **Data snooping and validation reuse:**
  - no validation segment exists, so no validation data has been seen;
  - development results for 2026-10-12 are now known, so thresholds must not
    be revised on the basis of them;
  - this campaign's parameters stay fixed for every later replication.
- **Unmodelled:** costs, slippage, funding payments, capacity, execution.

## Conclusion

**We learned nothing about the hypotheses yet, and that is the correct
result for one day of data.** The campaign itself is now in place:
- pre-registered hypotheses and baselines;
- a versioned incremental-comparison protocol;
- immutable, reproducible runs.

It can be re-executed unchanged as data accumulates.

**What's needed:**
- ≥ 14 qualifying shard days for development verdicts. From 2026-10-24,
  with continuous collection, the earliest is around 2026-11-07.
- ≥ 30 days for a validation verdict, around 2026-11-23.

Then create `-v2` experiment specs and campaign identical except for the
pinned shards (`--spec-refs` prints them), so every `hypothesis_hash` stays
the same. Run development first, and validation once.

**Next to investigate (as a new, separately registered campaign version, not
by changing this one):**
- whether to exclude unchanged-OI rows from the price-only reference;
- whether the 4h horizon tells the same story.

## Knowledge status

| Hypothesis | `hypothesis_hash` (dataset-independent) | Status |
|---|---|---|
| H1 — decline + OI decline, Long | `755b22856101d236f81477348e9081f253f419da5ecbf5abcd6b4babac35f3c8` | **tested — insufficient data** |
| H2 — decline + OI increase, Long | `559de30eab3d92071056a6aa9a0ca11882071d02a8d3eacdb1f8ad9391b8d510` | **tested — insufficient data** |
| H3 — rise + OI decline, Short | `5e9f65a643ce6f0da1bca2025730393f943a983c07be8da5fbfa08a5b7e2e437` | **tested — insufficient data** |
| H4 — rise + OI increase, Short | `d5cf63e568d5d093cec79666548adb7ba0c214237f68707b66ac844fcf60fdb0` | **tested — insufficient data** |

Status vocabulary for this and future campaigns:
- **untested**
- **tested — insufficient data**
- **preliminary support** or **preliminary contradiction** (directional
  development verdict)
- **failed** (validation contradicts or is inconclusive after preliminary
  support)
- **supported, needs independent replication** (validation agrees)
- **replicated** (agrees again on later, unseen days)

## Run records

| Run | Directory under `research/oink_corp/data/runs/` |
|---|---|
| H1 | `oi-price-1h-h1-long-oi-down-v1/20261024T164937721709Z-535331` |
| H2 | `oi-price-1h-h2-long-oi-up-v1/20261024T164937827125Z-2c5ddc` |
| H3 | `oi-price-1h-h3-short-oi-down-v1/20261024T164937896342Z-93cbf3` |
| H4 | `oi-price-1h-h4-short-oi-up-v1/20261024T164937957838Z-928125` |
| Price only, Long | `oi-price-1h-base-long-v1/20261024T164938019299Z-5f1287` |
| Price only, Short | `oi-price-1h-base-short-v1/20261024T164938078049Z-57e1c3` |
| H1 vs price only | `oi-price-1h-h1-vs-price-only-v1/20261024T164938145671Z-73f37b` |
| H2 vs price only | `oi-price-1h-h2-vs-price-only-v1/20261024T164938254498Z-46ea43` |
| H3 vs price only | `oi-price-1h-h3-vs-price-only-v1/20261024T164938356670Z-b73e3b` |
| H4 vs price only | `oi-price-1h-h4-vs-price-only-v1/20261024T164938451657Z-f07e63` |

Reproduce (Parquet only, from `apps/api`):

```bash
python -m app.scripts.run_campaign --check \
  --campaign ../../research/oink_corp/campaigns/oi-incremental-price-1h-v1.json
python -m app.scripts.run_campaign --segment development \
  --campaign ../../research/oink_corp/campaigns/oi-incremental-price-1h-v1.json \
  --data-dir ../../research/oink_corp/data
```

A re-run writes new run directories. Their verdicts and spreads must match
the records above: the bootstrap seeds derive from the spec hashes, and the
datasets are hash-pinned. Verified on 2026-10-24 by two fresh executions on a
copy of the shards: both were identical to each other and to these records.
The price-only baselines' hashes are `4834cccf7d09953a3f9ec5f6ec18d068e2e9693bbfad70343977625e81ac5cf2` (Long) and
`2dec7b4feb2c95753186cbfb3f7853924dfc8783caebb7198e36da73500e392d` (Short).
