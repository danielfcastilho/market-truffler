import Link from "next/link";

import { ScoreBreakdown } from "@/components/ranking-board";
import { QualificationExplanation } from "@/components/truffle-overview";
import {
  formatScore,
  historyHref,
  truffleEmoji,
  type DirectionComparison,
  type RankDirection,
  type RankingHistoryPage,
  type RankingSnapshotDetail,
  type RankingSnapshotSummary,
  type SnapshotComparison,
} from "@/lib/rankings";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";
import { cn } from "@/lib/utils";

/** LIVE / HISTORY: which ranking is on screen is always explicit. */
export function RankingModeSwitch({ mode }: { mode: "live" | "history" }) {
  return (
    <nav aria-label="Ranking mode" className="inline-flex rounded-lg border border-border p-0.5 font-mono text-xs">
      {(["live", "history"] as const).map((m) => (
        <Link
          key={m}
          href={m === "live" ? "/sniffer" : "/sniffer?view=history"}
          aria-current={mode === m ? "page" : undefined}
          className={cn(
            "rounded-md px-3 py-1 font-semibold tracking-wide uppercase",
            mode === m
              ? m === "live"
                ? "bg-primary text-primary-foreground"
                : "bg-amber-500 text-white"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {m}
        </Link>
      ))}
    </nav>
  );
}

function utcSeconds(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

function gapLabel(seconds: number): string {
  if (seconds === 0) return "the same frame (another publication)";
  const minutes = Math.round(seconds / 60);
  return minutes < 120 ? `${minutes} min earlier` : `${(minutes / 60).toFixed(1)} h earlier`;
}

/** Selected snapshot, its neighbours, a compact recent list, and the way back to Live. */
export function HistoryNavigator({
  detail,
  page,
}: {
  detail: RankingSnapshotDetail;
  page: RankingHistoryPage | null;
}) {
  const s = detail.snapshot;
  return (
    <section aria-label="Snapshot" className="space-y-2 rounded-xl border border-amber-500/50 bg-amber-500/5 p-4 text-sm">
      <p className="font-mono text-xs font-semibold tracking-wide text-amber-900 uppercase dark:text-amber-200">
        History — an immutable published ranking, not the live one
      </p>
      <p className="font-mono">
        Frame {formatFrameTimeUtc(s.frame_time)} · published {utcSeconds(s.published_at)} · snapshot #{s.id}
        {s.analysis === "recovery" && " · analysis recovered after a restart"}
      </p>
      <p className="text-xs text-muted-foreground">
        The frame time is the 5-minute boundary the ranking describes; it became available when
        published, not at the frame time.
      </p>
      <p className="font-mono text-xs text-muted-foreground">
        Long {s.long_candidate ?? "—"} / {s.long_qualification ?? "no policy"} · Short{" "}
        {s.short_candidate ?? "—"} / {s.short_qualification ?? "no policy"} · temporal contract v
        {s.temporal_contract_version ?? "?"}
      </p>
      <div className="flex flex-wrap gap-3 font-mono text-xs">
        {detail.previous ? (
          <Link href={historyHref(detail.previous.id)} className="text-primary hover:underline">
            ← Previous ({formatFrameTimeUtc(detail.previous.frame_time)})
          </Link>
        ) : (
          <span className="text-muted-foreground">← No earlier snapshot</span>
        )}
        {detail.next ? (
          <Link href={historyHref(detail.next.id)} className="text-primary hover:underline">
            Next ({formatFrameTimeUtc(detail.next.frame_time)}) →
          </Link>
        ) : (
          <span className="text-muted-foreground">Newest snapshot</span>
        )}
        <Link href="/sniffer" className="font-semibold text-primary hover:underline">
          Return to Live
        </Link>
      </div>
      {page && page.items.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Recent snapshots</summary>
          <ul className="mt-1 grid gap-0.5 font-mono sm:grid-cols-2">
            {page.items.map((item) => (
              <li key={item.id}>
                <Link
                  href={historyHref(item.id)}
                  aria-current={item.id === s.id ? "true" : undefined}
                  className={cn("hover:underline", item.id === s.id && "font-semibold text-foreground")}
                >
                  {formatFrameTimeUtc(item.frame_time)} · 🟢 {truffleCount(item, "long")} · 🔴{" "}
                  {truffleCount(item, "short")}
                </Link>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function truffleCount(item: RankingSnapshotSummary, direction: RankDirection): string {
  const count = direction === "long" ? item.long_truffles : item.short_truffles;
  const status = direction === "long" ? item.long_qualification_status : item.short_qualification_status;
  if (count != null) return String(count);
  return status ?? "—";
}

function DirectionChanges({ direction, c }: { direction: RankDirection; c: DirectionComparison }) {
  const label = direction === "long" ? "Long" : "Short";
  if (!c.available) {
    return (
      <li>
        {label}: comparison unavailable — {c.reason}
      </li>
    );
  }
  const t = c.truffles;
  return (
    <li data-comparison={direction} className="space-y-0.5">
      <p>
        <span className="font-semibold">{label}</span>
        {(c.model_changed || c.policy_changed) && (
          <span className="ml-2 text-destructive">
            {c.model_changed ? "model" : "policy"} changed — not directly comparable
          </span>
        )}
      </p>
      <p className="text-muted-foreground">
        Top {c.k}: {c.entered_top_k.length ? `entered ${c.entered_top_k.join(", ")}` : "no entries"}
        {c.left_top_k.length > 0 && ` · left ${c.left_top_k.join(", ")}`}
      </p>
      <p className="text-muted-foreground">
        {!("count" in t)
          ? `Truffles: not comparable (qualification ${t.status ?? "?"} now, ${t.previous_status ?? "?"} before)`
          : `${truffleEmoji(direction)} Truffles ${t.previous_count} → ${t.count}` +
            (t.gained.length ? ` · newly qualified ${t.gained.join(", ")}` : "") +
            (t.lost.length ? ` · no longer qualified ${t.lost.join(", ")}` : "")}
      </p>
    </li>
  );
}

/** What changed since the previous *available* published snapshot. */
export function ComparisonSummary({ comparison }: { comparison: SnapshotComparison }) {
  return (
    <section aria-label="Changes since the previous snapshot" className="rounded-xl border border-border p-4 font-mono text-xs">
      {!comparison.available ? (
        <p className="text-muted-foreground">No comparison: {comparison.reason}.</p>
      ) : (
        <>
          <p className={comparison.consecutive ? "" : "text-amber-800 dark:text-amber-200"}>
            Compared with the previous published snapshot, frame{" "}
            {formatFrameTimeUtc(comparison.previous_frame_time)} — {gapLabel(comparison.frame_gap_seconds)}
            {comparison.consecutive ? " (consecutive)" : " — not a consecutive 5-minute comparison"}
          </p>
          <ul className="mt-2 space-y-2">
            <DirectionChanges direction="long" c={comparison.long} />
            <DirectionChanges direction="short" c={comparison.short} />
          </ul>
        </>
      )}
    </section>
  );
}

/** One coin as published in the selected snapshot — never its live values. */
export function HistoricalCoinPanel({ detail, symbol }: { detail: RankingSnapshotDetail; symbol: string }) {
  return (
    <section aria-label={`${symbol} in this snapshot`} className="space-y-3 rounded-xl border border-border p-4 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-mono font-semibold">
          {symbol} · as published {utcSeconds(detail.snapshot.published_at)}
        </h3>
        <Link href={historyHref(detail.snapshot.id)} className="font-mono text-xs text-primary hover:underline">
          Close
        </Link>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {(["long", "short"] as const).map((direction) => {
          const ranking = direction === "long" ? detail.ranking.long : detail.ranking.short;
          const row = ranking?.rows.find((r) => r.symbol === symbol);
          return (
            <div key={direction} data-direction={direction} className="space-y-2">
              <p className="font-mono text-xs font-semibold uppercase">
                {truffleEmoji(direction)} {direction} · {ranking?.model?.candidate_id ?? "—"} ·{" "}
                {ranking?.qualification?.spec?.qualification_id ?? "no policy"}
              </p>
              {!ranking || !ranking.available ? (
                <p className="text-muted-foreground">Ranking unavailable in this snapshot.</p>
              ) : !row ? (
                <p className="text-muted-foreground">Not ranked in this snapshot.</p>
              ) : (
                <>
                  <p className="font-mono">
                    Rank #{row.rank} / {ranking.eligible} · score {formatScore(row.score)} ·{" "}
                    {row.qualification == null
                      ? "qualification withheld"
                      : row.qualification.qualified
                        ? "Truffle"
                        : "not a Truffle"}
                  </p>
                  <ScoreBreakdown row={row} />
                  {row.qualification && !row.qualification.checks ? (
                    <p className="text-xs text-muted-foreground">Qualification explanation not captured.</p>
                  ) : (
                    <QualificationExplanation row={row} qualification={ranking.qualification} />
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function HistoryEmpty() {
  return (
    <div className="max-w-xl rounded-lg border border-dashed border-border p-4 text-sm">
      <p className="font-medium">No published snapshot yet.</p>
      <p className="text-muted-foreground">
        Snapshots are captured from now on, each time Sniffer publishes a ranking. Earlier rankings
        were never stored and are not reconstructed.
      </p>
    </div>
  );
}
