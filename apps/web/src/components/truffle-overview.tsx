import Link from "next/link";

import {
  conditionLabel,
  truffleEmoji,
  type DirectionQualification,
  type LiveRankings,
  type RankDirection,
  type RankedCoin,
} from "@/lib/rankings";
import { cn } from "@/lib/utils";

/**
 * Experimental Truffles of the current frame, per direction: the coins that
 * meet that direction's qualification spec. Zero is an ordinary outcome;
 * "withheld" (stale/incomplete frame) and "unavailable" are stated as such.
 */
export function TruffleOverview({ data }: { data: LiveRankings }) {
  const both = new Set(data.truffles_in_both_directions ?? []);
  return (
    <section aria-label="Experimental Truffles" className="space-y-2">
      <div className="grid gap-3 md:grid-cols-2">
        {(["long", "short"] as const).map((direction) => (
          <TruffleCard
            key={direction}
            direction={direction}
            qualification={(direction === "long" ? data.long : data.short)?.qualification ?? null}
            both={both}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Truffle rules are experimental: they highlight extreme cases of each model&apos;s own
        thesis in this frame and do not establish predictive validity.
      </p>
    </section>
  );
}

function TruffleCard({
  direction,
  qualification,
  both,
}: {
  direction: RankDirection;
  qualification: DirectionQualification | null;
  both: Set<string>;
}) {
  const label = direction === "long" ? "Long" : "Short";
  const evaluated = qualification?.status === "evaluated";
  const count = evaluated ? qualification.truffles.length : null;
  return (
    <div
      data-truffles={direction}
      className={cn(
        "space-y-2 rounded-xl border p-4",
        direction === "long" ? "border-primary/40" : "border-destructive/40",
      )}
    >
      <p className="flex items-baseline justify-between font-mono text-sm font-semibold">
        <span>
          {truffleEmoji(direction)} Experimental {label} Truffles
        </span>
        <span className="text-lg">{count ?? "—"}</span>
      </p>
      {!qualification || qualification.status === "unavailable" ? (
        <p className="text-sm text-muted-foreground">
          Unavailable: {qualification?.reason ?? "no qualification spec configured"}
        </p>
      ) : qualification.status === "withheld" ? (
        <p className="text-sm text-muted-foreground">Withheld — {qualification.reason}</p>
      ) : count === 0 ? (
        <p className="text-sm text-muted-foreground">
          No experimental {label} Truffles qualified in this frame.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1.5 font-mono text-xs">
          {qualification.truffles.map((t) => (
            <li key={t.symbol}>
              <Link
                href={`/sniffer/${encodeURIComponent(t.symbol)}?direction=${direction}`}
                className="rounded-md bg-muted px-2 py-0.5 underline-offset-4 hover:underline"
                title={`Rank #${t.rank}`}
              >
                {t.symbol} <span className="text-muted-foreground">#{t.rank}</span>
                {both.has(t.symbol) && " · both"}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {qualification?.spec && (
        <p className="font-mono text-[11px] text-muted-foreground">
          {qualification.spec.qualification_id} · spec {qualification.spec.spec_hash.slice(0, 12)}…
        </p>
      )}
    </div>
  );
}

/** Why a row did or did not qualify, condition by condition. */
export function QualificationExplanation({
  row,
  qualification,
}: {
  row: RankedCoin;
  qualification: DirectionQualification | null | undefined;
}) {
  if (!qualification?.spec || qualification.status === "unavailable") {
    return (
      <p className="font-mono text-xs text-muted-foreground">
        Truffle qualification unavailable{qualification?.reason ? `: ${qualification.reason}` : ""}.
      </p>
    );
  }
  if (qualification.status === "withheld" || !row.qualification) {
    return (
      <p className="font-mono text-xs text-muted-foreground">
        Truffle qualification withheld{qualification.reason ? ` — ${qualification.reason}` : ""}.
      </p>
    );
  }
  const own = row.qualification;
  const observed = new Map((own.checks ?? []).map((c) => [c.id, c.observed]));
  return (
    <div className="space-y-1 font-mono text-xs">
      <p className={cn("font-semibold", own.qualified ? "text-foreground" : "text-muted-foreground")}>
        {own.qualified
          ? `${truffleEmoji(qualification.spec.direction)} Experimental Truffle — every condition passed`
          : "Not a Truffle"}{" "}
        <span className="font-normal text-muted-foreground">({qualification.spec.qualification_id})</span>
      </p>
      <ul className="space-y-0.5">
        {qualification.spec.conditions.map((c) => {
          const outcome = own.missing.includes(c.id)
            ? "missing"
            : own.failed.includes(c.id)
              ? "failed"
              : "passed";
          const value = observed.get(c.id);
          return (
            <li key={c.id} data-outcome={outcome} title={c.rationale}>
              {outcome === "passed" ? "✓" : outcome === "failed" ? "✗" : "?"} {conditionLabel(c)}
              {value != null && <span className="text-muted-foreground"> · observed {value.toFixed(4)}</span>}
              {outcome === "missing" && <span className="text-muted-foreground"> · input missing</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
