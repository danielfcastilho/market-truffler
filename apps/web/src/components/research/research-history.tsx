import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { ResearchActivity } from "@/components/research/research-activity";
import { VerdictBadge } from "@/components/research/verdict-badge";
import { formatTimestamp, type ResearchHistory as History } from "@/lib/research";

/** Every research event and every immutable run, for auditing. */
export function ResearchHistory({ history }: { history: History }) {
  return (
    <div className="max-w-5xl space-y-12">
      <ArtifactIssues issues={history.issues} />
      <section className="space-y-4">
        <h2 className="text-lg font-semibold tracking-tight">Research events</h2>
        <ResearchActivity events={history.events} />
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">
          Immutable runs ({history.runs.length})
        </h2>
        {history.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No experiment has been run yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
            <table className="w-full text-left font-mono text-xs">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Executed</th>
                  <th className="px-3 py-2 font-medium">Experiment / comparison</th>
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">Campaign</th>
                  <th className="px-3 py-2 font-medium">Direction</th>
                  <th className="px-3 py-2 font-medium">Segment</th>
                  <th className="px-3 py-2 font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {history.runs.map((run) => (
                  <tr key={`${run.experiment_id}/${run.run_id}`}>
                    <td className="px-3 py-2 whitespace-nowrap">{formatTimestamp(run.executed_at)}</td>
                    <td className="px-3 py-2">
                      {run.kind === "ranking_study" ? (
                        <Link href={`/oink-corp/ranking#${run.experiment_id}`} className="underline-offset-4 hover:underline">
                          {run.experiment_id}
                        </Link>
                      ) : run.link_experiment_id ? (
                        <Link href={`/oink-corp/experiments/${run.link_experiment_id}`} className="underline-offset-4 hover:underline">
                          {run.experiment_id}
                        </Link>
                      ) : (
                        run.experiment_id
                      )}
                      {run.kind !== "experiment" && (
                        <span className="ml-2 text-muted-foreground">
                          {run.kind === "prospective_evaluation"
                            ? "prospective"
                            : run.kind === "ranking_study"
                              ? "ranking"
                              : "comparison"}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{run.run_id}</td>
                    <td className="px-3 py-2">{run.campaign_id ?? "—"}</td>
                    <td className="px-3 py-2 capitalize">{run.direction ?? "—"}</td>
                    <td className="px-3 py-2">{run.segment ?? "—"}</td>
                    <td className="px-3 py-2">
                      <VerdictBadge verdict={run.verdict} failed={run.status !== "completed"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
