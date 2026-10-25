# Ranking diagnostics and experimental Truffle qualification

What the Sniffer Rankings page now adds on top of the experimental
Long/Short rankings, as implemented in `apps/api/app/ranking/diagnostics.py`,
`apps/api/app/ranking/qualification.py` and
`apps/api/app/services/live_rankings.py`.

## Ranking, qualification, validation

| | Question | Where |
| --- | --- | --- |
| Ranking | How does an eligible coin compare with the others under a model? | `app.ranking.scoring` (shared with research), unchanged |
| Qualification | Does it meet an explicit spec's conditions for being highlighted as a Truffle? | `app.ranking.qualification`, specs in `research/oink_corp/ranking/qualifications/` |
| Validation | Does independent evidence show that highlighting it is useful? | OINK CORP. **Not done for any ranking model or Truffle spec.** |

Qualification reads the ranking's eligible cross-section and never changes
a score or an order. It is not part of the scoring engine, and it reads no
labels, datasets or research code. A Truffle is a coin that met a spec's
conditions in one frame. It is not a validated signal or a trading
recommendation.

## The initial policy (`policy: "relative"`, v1)

Each spec applies to exactly one ranking candidate and direction. A coin
qualifies only if **every** condition passes. Bounds are inclusive. A
missing or non-finite input fails as `missing`; it never passes.

**Frame conditions.** Both directions; applied per request, because they
depend on the clock:

- `frame_fresh`: the ranking is not stale (same staleness rule as the
  rankings).
- `frame_completeness ≥ 0.95`: the frame covers at least 95% of the
  expected universe, so percentiles describe nearly the whole market.

If either fails, qualification is **withheld**: no Truffles are shown and
per-row flags are hidden. Withheld is not the same as zero.

**Long** (`long-reversal-oi-truffle-v1`, candidate `long-price-reversal-oi-v1`):

| id | condition | why |
| --- | --- | --- |
| `return_tail` | `return_1h` percentile ≤ 0.05 | the model's primary term (weight −1): weakest 5% this frame |
| `return_fell` | `return_1h` ≤ 0 | a reversal of a fall needs an actual fall, not just a relatively weak coin in a rally |
| `oi_tail` | `oi_change_1h` percentile ≤ 0.20 | the secondary term (weight −0.5); looser because its weight is half |
| `oi_fell` | `oi_change_1h` ≤ 0 | Open Interest did not grow |

**Short** (`short-reversal-oi-truffle-v1`, candidate `short-price-reversal-oi-v1`):

| id | condition | why |
| --- | --- | --- |
| `return_tail` | `return_1h` percentile ≥ 0.95 | primary term (weight +1): strongest 5% this frame |
| `return_rose` | `return_1h` ≥ 0 | an actual rise, not just a relatively strong coin in a sell-off |
| `oi_tail` | `oi_change_1h` percentile ≤ 0.20 | secondary term (weight −0.5): a rise without new positions |
| `oi_fell` | `oi_change_1h` ≤ 0 | Open Interest did not grow |

Percentiles are the same cross-sectional percentile the models use (the
fraction of the ranked universe with a value ≤ the coin's), computed over
the ranked universe.

The thresholds are round numbers written down before any evaluation. They
were not tuned against outcomes, and protected periods were never read.
The only data looked at was one live frame's Truffle *count*, observed
after the specs were written (12 Long, 14 Short of ~800). Nothing was
adjusted afterwards.

**Long and Short differ by construction.** Each mirrors its own model:
Long needs a fall, Short a rise, and both need falling Open Interest. They
are not inverses of each other. With these two specs a coin cannot qualify
for both in one frame, because its 1h return can't be in both the weakest
and the strongest 5%. If future specs allow it, the coin is listed under
both (`truffles_in_both_directions`), and neither side is dropped.

## Why rank 1 may not qualify, and why zero Truffles is valid

Rank is not a condition. The top score can come from a coin that is
merely good on both terms but extreme on neither, so it misses a tail
condition. It can also come from a coin that is extreme only *relative* to
the frame: in a broad rally, the weakest coin may still have risen, which
fails `return_fell`.

Zero Truffles is an ordinary, evaluated outcome ("No experimental Long
Truffles qualified in this frame"). It occurs when no coin is extreme on
every term at once with the right signs, for example in a market-wide
move. With ~800 coins and these tails, small non-zero counts are the
usual case. That is a property of a relative policy, and it is disclosed
rather than tuned away.

## Limitations

- **Relative policy.** Every condition except the sign checks is relative
  to the frame. Every frame has a weakest 5%, so qualification cannot tell
  an exceptional market from an ordinary one; the sign checks are the only
  absolute guard. Truffle counts scale with universe size.
- **Unvalidated thesis.** The models' thesis (reversal) is itself
  unvalidated. The exploratory 2026-10-12 development results pointed to
  continuation instead (`research/oink_corp/ranking/README.md`), and a
  Truffle inherits that.
- **No outcome measurement.** Nothing measures whether Truffles do better
  than other coins. That needs a pre-registered OINK CORP evaluation of the
  qualification spec on data it has never seen, with costs disclosed and
  the multiple-testing requirements in `ARCHITECTURE.md` respected.

## Diagnostics

Computed per direction from the ranking snapshot, once per analyzed frame
(see "Performance").

- **Distribution** of the eligible universe's scores: count, min, max,
  mean, median, population standard deviation, spread (max − min),
  percentiles p10/p25/p75/p90/p99 (linear interpolation), and a 12-bin
  equal-width histogram.
- **Separation:**
  - `#1 − #2` and `#1 − #10` (None with fewer coins);
  - mean of the top 10 minus the mean of the rest (None without a full
    top 10 plus one more coin);
  - **near-top count:** the number of coins within 10% of the score spread
    of #1. A crowded top means rank 1 is barely distinguishable from its
    neighbours.
- **Stability** against the frame exactly one boundary earlier (T−5m),
  re-ranked by **the same candidate spec** from that frame's stored
  Sniffer features. Reported: top-10 overlap, entrants, leavers, rank
  changes of the current top 10, and Spearman's ρ over the coins ranked
  in both. It is unavailable, with the reason, when:
  - the previous frame was never analyzed ("insufficient history");
  - it was measured under another temporal contract version ("not
    comparable");
  - it had fewer than 50 eligible coins.

  An older or partial frame is never substituted.

Every diagnostic carries the frame time, candidate id, model spec hash,
eligible count, temporal contract version and its limitations. For these
models a score is a sum of within-frame percentiles, so its range is fixed
by construction and separation mostly reflects how the features co-move.
Diagnostics describe a ranking; they are not opportunity quality or
expected profitability.

## Temporal constraints

Qualification and diagnostics read only what the ranking snapshot read:
the analyzed frame's Sniffer features, which already follow
[TEMPORAL_CONTRACT.md](TEMPORAL_CONTRACT.md). Stability's previous frame
is strictly earlier and is ranked with the current spec. Both frames are
measured by one model, so the comparison never mixes model versions. It
is refused across temporal contract versions. No forward return, dataset
or reserved period is read (enforced by an import-boundary test).

## API

`GET /api/sniffer/rankings` (unchanged path; additive fields):

- `long` / `short`:
  - `diagnostics`;
  - `qualification`: `status` (`evaluated` | `withheld` | `unavailable`),
    `reason`, `spec` (id, hash, conditions, status), `evaluated`,
    `qualified`, `truffles` (rank, symbol, score) and `frame_checks`.
- each row: `qualification` = `{qualified, failed, missing}`, or null
  when withheld.
- `frame.temporal_contract_version`, plus top-level
  `truffles_in_both_directions`.
- `?symbol=X` additionally returns that row's full `checks` (observed
  value, threshold, outcome per condition).

## History

Every published ranking, with its Truffles and full qualification
explanations, is kept as an immutable snapshot and browsable under Rankings
→ History. See [RANKING_SNAPSHOTS.md](RANKING_SNAPSHOTS.md).

## Performance

The clock-independent part (both rankings, diagnostics, qualification
checks) is computed once per analyzed frame and cached in-process. The
cache is keyed by the frame's analysis marker and the spec files' bytes.
Requests then apply freshness and the frame gates. Measured on a live
frame of ~800 coins: 0.18s to compute, about 2 ms per cached request.
Stability loads only the candidate's features of the previous frame.
