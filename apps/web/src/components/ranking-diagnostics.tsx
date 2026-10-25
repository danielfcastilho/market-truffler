import type { RankingDiagnostics } from "@/lib/rankings";
import { formatScore } from "@/lib/rankings";
import { formatFrameTimeUtc } from "@/lib/sniffer-format";

/**
 * Compact, descriptive diagnostics of one direction's ranking: how scores
 * are spread, how far the top stands out, and how much the top changed since
 * the previous frame. Everything is computed by the backend; this only lays
 * it out.
 */
export function RankingDiagnosticsPanel({ diagnostics }: { diagnostics: RankingDiagnostics | null | undefined }) {
  if (!diagnostics) return null;
  if (!diagnostics.available || !diagnostics.distribution || !diagnostics.separation) {
    return (
      <p className="font-mono text-xs text-muted-foreground">
        Diagnostics unavailable{diagnostics.reason ? `: ${diagnostics.reason}` : ""}.
      </p>
    );
  }
  const d = diagnostics.distribution;
  const s = diagnostics.separation;
  const st = diagnostics.stability;
  const peak = Math.max(...d.histogram.counts, 1);
  const num = (v: number | null | undefined) => (v == null ? "—" : formatScore(v));
  return (
    <details className="rounded-xl border border-border p-4 text-sm" data-testid="ranking-diagnostics">
      <summary className="cursor-pointer font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        Ranking diagnostics · {diagnostics.eligible} eligible
      </summary>
      <div className="mt-3 grid gap-4 md:grid-cols-3">
        <div className="space-y-2">
          <p className="font-mono text-xs font-medium">Score distribution</p>
          <div className="flex h-12 items-end gap-0.5" aria-label="Score histogram, low to high">
            {d.histogram.counts.map((n, i) => (
              <div
                key={i}
                className="flex-1 rounded-sm bg-primary/50"
                style={{ height: `${Math.max((n / peak) * 100, n ? 4 : 0)}%` }}
                title={`${n} instruments`}
              />
            ))}
          </div>
          <dl className="grid grid-cols-2 gap-x-3 font-mono text-xs">
            <dt className="text-muted-foreground">min / max</dt>
            <dd>
              {num(d.min)} / {num(d.max)}
            </dd>
            <dt className="text-muted-foreground">median</dt>
            <dd>{num(d.median)}</dd>
            <dt className="text-muted-foreground">std</dt>
            <dd>{d.std.toFixed(3)}</dd>
            <dt className="text-muted-foreground">p10 / p90</dt>
            <dd>
              {num(d.percentiles.p10)} / {num(d.percentiles.p90)}
            </dd>
          </dl>
        </div>
        <div className="space-y-2">
          <p className="font-mono text-xs font-medium">Separation at the top</p>
          <dl className="grid grid-cols-2 gap-x-3 font-mono text-xs">
            <dt className="text-muted-foreground">#1 − #2</dt>
            <dd>{num(s.top1_minus_top2)}</dd>
            <dt className="text-muted-foreground">#1 − #10</dt>
            <dd>{num(s.top1_minus_top10)}</dd>
            <dt className="text-muted-foreground">top {s.k} − rest</dt>
            <dd>{num(s.top_k_mean_minus_rest_mean)}</dd>
            <dt className="text-muted-foreground">near the top</dt>
            <dd title={`within ${s.near_top_fraction * 100}% of the score spread of #1`}>
              {s.near_top_count}
            </dd>
          </dl>
        </div>
        <div className="space-y-2">
          <p className="font-mono text-xs font-medium">Stability vs previous frame</p>
          {!st?.available ? (
            <p className="font-mono text-xs text-muted-foreground">{st?.reason ?? "unavailable"}</p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-3 font-mono text-xs">
              <dt className="text-muted-foreground">frame</dt>
              <dd>{formatFrameTimeUtc(st.previous_frame_time)}</dd>
              <dt className="text-muted-foreground">top {st.k} overlap</dt>
              <dd>
                {st.top_k_overlap} / {st.k}
              </dd>
              <dt className="text-muted-foreground">rank correlation</dt>
              <dd>{st.rank_correlation == null ? "—" : st.rank_correlation.toFixed(2)}</dd>
              <dt className="text-muted-foreground">entered</dt>
              <dd>{st.entered_top_k?.length ? st.entered_top_k.join(", ") : "—"}</dd>
            </dl>
          )}
        </div>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Descriptive only — score spread is not expected profitability. Temporal contract v
        {diagnostics.temporal_contract_version} · spec {diagnostics.spec_hash?.slice(0, 12)}…
      </p>
    </details>
  );
}
