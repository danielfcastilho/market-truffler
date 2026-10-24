"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";

import { Input } from "@/components/ui/input";
import {
  featureClassName,
  featureLabel,
  formatFeature,
  formatScore,
  transformLabel,
  type DirectionRanking,
  type LiveModel,
  type RankDirection,
  type RankedCoin,
} from "@/lib/rankings";
import type { LiveRankingsResult } from "@/lib/server-api";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";
import { cn } from "@/lib/utils";

const CONTEXT = ["return_1h", "oi_change_1h", "relative_volume_1h"] as const;

/**
 * Sniffer's experimental Long/Short rankings: the latest frame's coins in
 * the backend's order (rank 1 = most attractive for the direction under the
 * configured model), with the model and every score's breakdown one click
 * away. Nothing is re-sorted or recomputed here; search only filters.
 */
export function RankingBoard({
  result,
  initialDirection = "long",
}: {
  result: LiveRankingsResult;
  initialDirection?: RankDirection;
}) {
  const [direction, setDirection] = useState<RankDirection>(initialDirection);
  const [query, setQuery] = useState("");

  if (result.status === "error") {
    return (
      <div role="alert" className="max-w-xl rounded-lg border border-destructive/50 p-4 text-sm">
        <p className="font-medium text-destructive">Rankings could not be loaded.</p>
        <p className="font-mono text-xs text-muted-foreground">{result.message}</p>
      </div>
    );
  }
  const data = result.data;
  if (!data.available || !data.frame) {
    return (
      <div className="max-w-xl rounded-lg border border-dashed border-border p-4 text-sm">
        <p className="font-medium">No rankings right now.</p>
        <p className="text-muted-foreground">{data.reason}</p>
      </div>
    );
  }
  const frame = data.frame;
  const ranking = direction === "long" ? data.long : data.short;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 font-mono text-sm text-muted-foreground">
        <span>
          Frame {formatFrameTimeUtc(frame.frame_time)} · {frame.analyzed_instruments} instruments
        </span>
        <span
          className={cn(
            "rounded-md px-2 py-0.5 text-xs",
            frame.stale ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary",
          )}
          title={frame.stale_reason ?? undefined}
        >
          {frame.stale ? `Stale — ${frame.stale_reason}` : "Current"}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">Experimental rankings.</span> Models are
        manually specified and not independently validated — a high rank is not a probability of
        profit, and nothing here is a trading signal.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Direction" className="inline-flex rounded-lg border border-border p-0.5">
          {(["long", "short"] as const).map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={direction === d}
              onClick={() => setDirection(d)}
              className={cn(
                "rounded-md px-4 py-1.5 text-sm font-semibold tracking-wide transition-colors",
                direction === d
                  ? d === "long"
                    ? "bg-primary text-primary-foreground"
                    : "bg-destructive text-white"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {d === "long" ? "🟢 LONG" : "🔴 SHORT"}
            </button>
          ))}
        </div>
        <Input
          aria-label="Search symbol"
          placeholder="Search symbol…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="max-w-xs"
        />
      </div>

      {ranking === null || !ranking.available ? (
        <div className="space-y-2 rounded-lg border border-dashed border-border p-4 text-sm">
          <p className="font-medium">The {direction} ranking is unavailable.</p>
          <p className="text-muted-foreground">{ranking?.reason ?? "No model is configured."}</p>
        </div>
      ) : (
        <>
          <ModelPanel model={ranking.model!} ranking={ranking} />
          <RankingTable ranking={ranking} direction={direction} query={query} />
        </>
      )}
    </div>
  );
}

function ModelPanel({ model, ranking }: { model: LiveModel; ranking: DirectionRanking }) {
  return (
    <details className="rounded-xl border border-border p-4 text-sm">
      <summary className="cursor-pointer">
        <span className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {model.direction} model
        </span>{" "}
        <span className="font-medium">{model.name}</span>{" "}
        <span className="font-mono text-xs text-muted-foreground">
          · {model.model_family} · experimental
        </span>
      </summary>
      <div className="mt-3 space-y-3">
        <table className="text-left font-mono text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-4 font-medium">Feature</th>
              <th className="py-1 pr-4 font-medium">Transformation</th>
              <th className="py-1 font-medium">Weight</th>
            </tr>
          </thead>
          <tbody>
            {model.terms.map((t) => (
              <tr key={t.feature}>
                <td className="py-1 pr-4">{featureLabel(t.feature)}</td>
                <td className="py-1 pr-4">{transformLabel(t.transform)}</td>
                <td className="py-1">{t.weight > 0 ? `+${t.weight}` : t.weight}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-muted-foreground">
          Score = Σ weight × transformed value, computed across the {ranking.eligible} eligible
          coins of this frame. A higher score means more attractive for{" "}
          {model.direction === "long" ? "a Long" : "a Short"} under this model&apos;s construction
          — not a probability of profit.
        </p>
        <p className="text-muted-foreground">
          Coins missing any of these features are excluded ({ranking.excluded.length}
          {ranking.excluded.length > 0 &&
            `: ${ranking.excluded
              .slice(0, 8)
              .map((e) => e.symbol)
              .join(", ")}${ranking.excluded.length > 8 ? ", …" : ""}`}
          ).
          {ranking.degenerate_features.length > 0 &&
            ` Without variation this frame (contributes 0): ${ranking.degenerate_features.join(", ")}.`}
        </p>
        <p className="text-muted-foreground">
          Status: {model.validation_status}.{" "}
          <Link href="/oink-corp/ranking" className="text-primary underline-offset-4 hover:underline">
            Research behind this model →
          </Link>
        </p>
        <p className="font-mono text-xs text-muted-foreground">
          {model.candidate_id} · spec {model.spec_hash.slice(0, 12)}…
        </p>
      </div>
    </details>
  );
}

function RankingTable({
  ranking,
  direction,
  query,
}: {
  ranking: DirectionRanking;
  direction: RankDirection;
  query: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = useMemo(() => {
    const q = query.trim().toUpperCase();
    return q ? ranking.rows.filter((r) => r.symbol.includes(q)) : ranking.rows;
  }, [ranking.rows, query]);

  if (rows.length === 0) {
    return <p className="font-mono text-sm text-muted-foreground">No symbol matches “{query}”.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
      <table className="w-full text-left font-mono text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Rank</th>
            <th className="px-3 py-2 font-medium">Symbol</th>
            <th className="px-3 py-2 text-right font-medium">Score</th>
            {CONTEXT.map((f) => (
              <th key={f} className="hidden px-3 py-2 text-right font-medium sm:table-cell">
                {featureLabel(f)}
              </th>
            ))}
            <th className="px-3 py-2 font-medium">
              <span className="sr-only">Details</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {rows.map((row) => (
            <Fragment key={row.symbol}>
              <tr data-symbol={row.symbol}>
                <td className="px-3 py-2 text-muted-foreground">#{row.rank}</td>
                <td className="px-3 py-2">
                  <Link
                    href={`/sniffer/${encodeURIComponent(row.symbol)}?direction=${direction}`}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {row.symbol}
                  </Link>
                </td>
                <td className="px-3 py-2 text-right font-medium">{formatScore(row.score)}</td>
                {CONTEXT.map((f) => (
                  <td
                    key={f}
                    className={cn(
                      "hidden px-3 py-2 text-right sm:table-cell",
                      featureClassName(f, row.context[f]),
                    )}
                  >
                    {formatFeature(f, row.context[f])}
                  </td>
                ))}
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    aria-expanded={open === row.symbol}
                    onClick={() => setOpen(open === row.symbol ? null : row.symbol)}
                    className="text-xs text-primary underline-offset-4 hover:underline"
                  >
                    Why?
                  </button>
                </td>
              </tr>
              {open === row.symbol && (
                <tr>
                  <td colSpan={4 + CONTEXT.length} className="bg-muted/30 px-3 py-3">
                    <ScoreBreakdown row={row} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** How the model produced one score; the contributions sum to it. */
export function ScoreBreakdown({ row }: { row: RankedCoin }) {
  return (
    <table className="w-full max-w-2xl text-left font-mono text-xs">
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-1 pr-3 font-medium">Feature</th>
          <th className="py-1 pr-3 text-right font-medium">Value</th>
          <th className="py-1 pr-3 text-right font-medium">Transformed</th>
          <th className="py-1 pr-3 text-right font-medium">Weight</th>
          <th className="py-1 text-right font-medium">Contribution</th>
        </tr>
      </thead>
      <tbody>
        {row.contributions.map((c) => (
          <tr key={c.feature}>
            <td className="py-1 pr-3">
              {featureLabel(c.feature)}{" "}
              <span className="text-muted-foreground">({transformLabel(c.transform)})</span>
            </td>
            <td className="py-1 pr-3 text-right">{formatFeature(c.feature, c.raw)}</td>
            <td className="py-1 pr-3 text-right">{c.transformed.toFixed(3)}</td>
            <td className="py-1 pr-3 text-right">{c.weight}</td>
            <td className="py-1 text-right">{formatScore(c.contribution)}</td>
          </tr>
        ))}
        <tr className="border-t border-border font-semibold">
          <td className="pt-1 pr-3" colSpan={4}>
            Score
          </td>
          <td className="pt-1 text-right">{formatScore(row.score)}</td>
        </tr>
      </tbody>
    </table>
  );
}
