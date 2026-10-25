import Link from "next/link";

import { ScoreBreakdown } from "@/components/ranking-board";
import { QualificationExplanation } from "@/components/truffle-overview";
import { formatScore, type DirectionRanking, type RankDirection } from "@/lib/rankings";
import type { LiveRankingsResult } from "@/lib/server-api";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";

/**
 * Where one coin sits in the experimental Long and Short rankings of the
 * latest frame, and how each score was produced. Read from the same
 * ranking snapshot as the Rankings tab; nothing is recomputed.
 */
export function RankingContext({
  symbol,
  rankings,
  backDirection,
}: {
  symbol: string;
  rankings: LiveRankingsResult;
  backDirection: RankDirection;
}) {
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-mono text-sm font-medium text-foreground">Experimental rankings</h3>
        <Link
          href={`/sniffer?direction=${backDirection}`}
          className="font-mono text-xs text-primary underline-offset-4 hover:underline"
        >
          ← Back to {backDirection} rankings
        </Link>
      </div>
      {rankings.status === "error" ? (
        <p className="font-mono text-sm text-destructive">
          Rankings could not be loaded: {rankings.message}
        </p>
      ) : !rankings.data.available || !rankings.data.frame ? (
        <p className="font-mono text-sm text-muted-foreground">{rankings.data.reason}</p>
      ) : (
        <>
          <p className="font-mono text-xs text-muted-foreground">
            Frame {formatFrameTimeUtc(rankings.data.frame.frame_time)}
            {rankings.data.frame.stale && " · stale"} · models not independently validated
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            {(["long", "short"] as const).map((direction) => (
              <DirectionCard
                key={direction}
                direction={direction}
                symbol={symbol}
                ranking={direction === "long" ? rankings.data.long : rankings.data.short}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function DirectionCard({
  direction,
  symbol,
  ranking,
}: {
  direction: RankDirection;
  symbol: string;
  ranking: DirectionRanking | null;
}) {
  const row = ranking?.rows.find((r) => r.symbol === symbol);
  const excluded = ranking?.excluded.find((e) => e.symbol === symbol);
  return (
    <div className="space-y-3 rounded-xl border border-border p-4" data-direction={direction}>
      <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {direction === "long" ? "🟢 Long" : "🔴 Short"}
        {ranking?.model && ` · ${ranking.model.name}`}
      </p>
      {!ranking || !ranking.available ? (
        <p className="font-mono text-sm text-muted-foreground">
          Unavailable: {ranking?.reason ?? "no model configured"}
        </p>
      ) : row ? (
        <>
          <p className="font-mono text-sm">
            Rank <span className="text-lg font-semibold">#{row.rank}</span> / {ranking.eligible} ·
            Score <span className="font-semibold">{formatScore(row.score)}</span>
          </p>
          <ScoreBreakdown row={row} />
          <QualificationExplanation row={row} qualification={ranking.qualification} />
        </>
      ) : (
        <p className="font-mono text-sm text-muted-foreground">
          Not ranked
          {excluded
            ? ` — missing ${excluded.missing.join(", ")} in this frame`
            : " — not in the latest analyzed frame"}
          .
        </p>
      )}
    </div>
  );
}
