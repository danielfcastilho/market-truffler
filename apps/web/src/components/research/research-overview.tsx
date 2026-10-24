import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  VERDICTS,
  VERDICT_META,
  formatTimestamp,
  type ResearchOverview as Overview,
  type Verdict,
} from "@/lib/research";

/**
 * What OINK CORP is investigating and what it has (and hasn't) found:
 * counts of real artifacts, registered campaigns with the recorded
 * verdicts of their primary comparisons, and the latest runs.
 */
export function ResearchOverview({ overview }: { overview: Overview }) {
  const { counts, campaigns, recent_runs: recent } = overview;
  if (counts.campaigns === 0 && counts.runs === 0 && counts.experiments_registered === 0) {
    return (
      <div className="max-w-xl space-y-2">
        <p className="font-medium">No research has been registered yet.</p>
        <p className="text-sm text-muted-foreground">
          Campaigns appear here once a pre-registration file exists in{" "}
          <code className="font-mono">research/oink_corp/campaigns/</code>, and results once its
          experiments have been run. Nothing is shown until then.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl space-y-10">
      <ArtifactIssues issues={overview.issues} />

      <section className="space-y-3">
        <dl className="grid grid-cols-2 gap-3 font-mono text-sm sm:grid-cols-4">
          <Stat label="Campaigns" value={counts.campaigns} />
          <Stat label="Pre-registered hypotheses" value={counts.hypotheses} />
          <Stat
            label="Experiments run / registered"
            value={`${counts.experiments_executed} / ${counts.experiments_registered}`}
          />
          <Stat
            label="Comparisons run / registered"
            value={`${counts.comparisons_executed} / ${counts.comparisons_registered}`}
          />
        </dl>
        <div className="rounded-lg border border-border p-3 font-mono text-xs">
          <p className="mb-2 text-muted-foreground">
            Latest verdict of each experiment and comparison ({counts.runs} runs recorded
            {counts.runs_failed ? `, ${counts.runs_failed} failed` : ""}; repeated runs are not
            counted twice)
          </p>
          {(["development", "validation"] as const).map((segment) => (
            <VerdictCounts
              key={segment}
              segment={segment}
              counts={counts.latest_verdicts[segment]}
            />
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Activity is not success: these numbers describe work done, not findings.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">Research campaigns</h2>
        {campaigns.length === 0 ? (
          <p className="text-sm text-muted-foreground">No campaigns registered.</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {campaigns.map((campaign) => (
              <Link
                key={campaign.campaign_id}
                href={`/oink-corp/campaigns/${campaign.campaign_id}`}
                className="block space-y-3 rounded-xl p-4 ring-1 ring-foreground/10 transition-colors hover:bg-muted/50"
              >
                <div className="font-mono text-xs text-muted-foreground">
                  {campaign.campaign_id} · registered {campaign.created ?? "—"}
                </div>
                <p className="font-medium leading-snug">{campaign.question}</p>
                <CampaignStatus campaign={campaign} />
                <div className="font-mono text-xs text-muted-foreground">
                  {campaign.hypotheses} hypotheses · {campaign.experiments} experiments ·{" "}
                  latest activity {formatTimestamp(campaign.latest_activity)}
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">Recent research</h2>
        {recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No experiment has been run yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead className="text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="py-2 pr-4 font-medium">Executed</th>
                  <th className="py-2 pr-4 font-medium">Experiment / comparison</th>
                  <th className="py-2 pr-4 font-medium">Campaign</th>
                  <th className="py-2 pr-4 font-medium">Direction</th>
                  <th className="py-2 pr-4 font-medium">Segment</th>
                  <th className="py-2 font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {recent.map((run) => (
                  <tr key={`${run.experiment_id}/${run.run_id}`}>
                    <td className="py-2 pr-4 whitespace-nowrap">{formatTimestamp(run.executed_at)}</td>
                    <td className="py-2 pr-4">
                      {run.link_experiment_id ? (
                        <Link
                          href={`/oink-corp/experiments/${run.link_experiment_id}`}
                          className="underline-offset-4 hover:underline"
                        >
                          {run.experiment_id}
                        </Link>
                      ) : (
                        run.experiment_id
                      )}
                      {run.kind === "incremental_comparison" && (
                        <span className="ml-2 text-muted-foreground">comparison</span>
                      )}
                    </td>
                    <td className="py-2 pr-4">{run.campaign_id ?? "—"}</td>
                    <td className="py-2 pr-4 capitalize">{run.direction ?? "—"}</td>
                    <td className="py-2 pr-4">{run.segment ?? "—"}</td>
                    <td className="py-2">
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

function CampaignStatus({ campaign }: { campaign: Overview["campaigns"][number] }) {
  if (campaign.comparisons_executed === 0) {
    return <p className="text-sm text-muted-foreground">Registered — not executed yet.</p>;
  }
  const present = VERDICTS.filter((v) => campaign.development_verdicts[v] > 0);
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {present.map((verdict) => (
          <span key={verdict} className="inline-flex items-center gap-1">
            <VerdictBadge verdict={verdict} />
            <span className="font-mono text-xs text-muted-foreground">
              × {campaign.development_verdicts[verdict]}
            </span>
          </span>
        ))}
      </div>
      <p className="font-mono text-xs text-muted-foreground">
        {campaign.comparisons_executed} of {campaign.comparisons} primary comparisons run
        (development) ·{" "}
        {campaign.comparisons_validated === 0
          ? "no validation run"
          : `${campaign.comparisons_validated} validated`}
      </p>
    </div>
  );
}

function VerdictCounts({
  segment,
  counts,
}: {
  segment: "development" | "validation";
  counts: Record<Verdict, number> | undefined;
}) {
  const total = counts ? VERDICTS.reduce((sum, v) => sum + counts[v], 0) : 0;
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-1">
      <span className="w-24 capitalize">{segment}</span>
      {total === 0 ? (
        <span className="text-muted-foreground">none</span>
      ) : (
        VERDICTS.map((verdict) => (
          <span key={verdict} className={counts![verdict] ? "" : "text-muted-foreground/60"}>
            {VERDICT_META[verdict].label}: {counts![verdict]}
          </span>
        ))
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-lg font-semibold">{value}</dd>
    </div>
  );
}
