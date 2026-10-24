import {
  PARTITION_STATE_LABEL,
  formatTimestamp,
  shortHash,
  type EvaluationSummary,
} from "@/lib/research";
import { cn } from "@/lib/utils";

/**
 * Prospective evaluations registered for a campaign: which claims are
 * frozen, which future periods are reserved for validation and the
 * protected holdout, their state, and every recorded exposure. A
 * registered plan is not evidence; this says what has — and hasn't — been
 * tested on unseen data.
 */
export function IndependentTesting({ evaluations }: { evaluations: EvaluationSummary[] }) {
  if (evaluations.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
        No prospective evaluation is registered: every result for this campaign comes from
        historical data, so none of it is independent confirmation.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {evaluations.map((evaluation) => (
        <article
          key={evaluation.registration_id}
          className="space-y-4 rounded-2xl bg-card p-5 ring-1 ring-foreground/10"
        >
          <header className="space-y-1">
            <p className="font-mono text-xs text-muted-foreground">
              Registration {evaluation.registration_id} · protocol v{evaluation.protocol_version}
              {evaluation.supersedes && ` · supersedes ${evaluation.supersedes}`}
            </p>
            <p className="text-sm">
              Registered {formatTimestamp(evaluation.registered_at)}, freezing{" "}
              {evaluation.hypotheses.join(", ")} — their predictions, comparisons, specifications,
              and the Evaluator&apos;s rules.
            </p>
          </header>
          <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50 font-mono text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Role</th>
                  <th className="px-3 py-2 font-medium">Reserved period (UTC)</th>
                  <th className="px-3 py-2 font-medium">State</th>
                  <th className="px-3 py-2 font-medium">Daily data</th>
                  <th className="px-3 py-2 font-medium">Exposure</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {evaluation.partitions.map((p) => (
                  <tr key={p.role}>
                    <td className="px-3 py-2 font-medium">
                      {p.role === "holdout" ? "Protected holdout" : "Validation"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {p.start} → {p.end} (exclusive)
                    </td>
                    <td className="px-3 py-2">
                      {PARTITION_STATE_LABEL[p.state] ?? p.state}
                      {p.state === "awaiting_data" && (
                        <span className="block text-xs text-muted-foreground">
                          eligible from {p.eligible_from.slice(0, 10)}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {p.days_with_shard} / {p.days} days
                    </td>
                    <td
                      className={cn(
                        "px-3 py-2 text-xs",
                        p.exposed ? "font-medium text-destructive" : "text-muted-foreground",
                      )}
                    >
                      {p.exposed
                        ? p.role === "holdout"
                          ? "Holdout exposed — it can never be re-run"
                          : `Exposed (${p.attempts} attempt${p.attempts === 1 ? "" : "s"}) — re-runs are not independent`
                        : p.role === "holdout"
                          ? "Not yet accessed"
                          : "Not yet exposed"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            Until a partition is evaluated, ordinary research cannot load data from it. The
            holdout needs an explicit protected invocation and runs at most once.{" "}
            {evaluation.timestamp_trust}
          </p>
          {evaluation.events && evaluation.events.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer font-mono text-xs text-muted-foreground">
                Exposure history ({evaluation.events.length} events)
              </summary>
              <ol className="mt-2 space-y-1 font-mono text-xs">
                {evaluation.events.map((e) => (
                  <li key={e.seq}>
                    #{e.seq} {formatTimestamp(e.at)} · {e.role} · {e.type}
                    {e.independence && ` · ${e.independence}`}
                    {e.error && <span className="text-destructive"> · {e.error}</span>}
                  </li>
                ))}
              </ol>
            </details>
          )}
          <p className="font-mono text-xs text-muted-foreground">
            registration hash {shortHash(evaluation.registration_hash)}
          </p>
        </article>
      ))}
    </div>
  );
}
