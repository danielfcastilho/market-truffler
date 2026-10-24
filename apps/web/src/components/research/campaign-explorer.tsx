import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { ConditionList } from "@/components/research/condition-list";
import { ReportText } from "@/components/research/report-text";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  VERDICTS,
  VERDICT_META,
  describeLabel,
  directionNote,
  evidenceFigures,
  formatCount,
  formatSpread,
  latestBySegment,
  segmentLabel,
  verdictOf,
  type CampaignDetail,
  type RunRecord,
  type Verdict,
} from "@/lib/research";

/**
 * One pre-registered campaign, from question to recorded conclusion.
 * Verdicts are the runs' own; the conclusion is the campaign report's own
 * text — this page computes neither.
 */
export function CampaignExplorer({ detail }: { detail: CampaignDetail }) {
  const { campaign, specs, comparison_runs: comparisonRuns, experiment_runs: experimentRuns } =
    detail;
  const sections = detail.report ?? [];
  const recorded = sections.filter((s) => s.title === "Conclusion" || s.title === "Knowledge status");

  return (
    <div className="max-w-4xl space-y-10">
      <ArtifactIssues issues={detail.issues} />

      <header className="space-y-3">
        <p className="font-mono text-xs text-muted-foreground">
          Campaign {campaign.campaign_id} · registered {campaign.created}
        </p>
        <h2 className="text-xl leading-snug font-semibold tracking-tight">{campaign.question}</h2>
        <EvidenceStatus detail={detail} />
      </header>

      <Section title="Why this was proposed">
        <p className="text-sm leading-relaxed">{campaign.motivation}</p>
      </Section>

      <Section title={`Hypotheses (${campaign.hypotheses.length}, pre-registered)`}>
        <div className="space-y-4">
          {campaign.hypotheses.map((hypothesis) => {
            const spec = specs[hypothesis.experiment_id];
            const comparison = campaign.comparisons.find((c) => c.hypothesis === hypothesis.id);
            const latest = latestBySegment(comparison ? (comparisonRuns[comparison.comparison_id] ?? []) : []);
            return (
              <article key={hypothesis.id} className="space-y-3 rounded-xl p-4 ring-1 ring-foreground/10">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-sm font-semibold">{hypothesis.id}</span>
                  {spec && (
                    <span className="rounded border border-border px-1.5 font-mono text-xs uppercase">
                      {spec.hypothesis.direction}
                    </span>
                  )}
                </div>
                <p className="text-sm leading-relaxed">{hypothesis.statement}</p>
                <p className="text-sm">
                  <span className="font-mono text-xs text-muted-foreground uppercase">
                    Prediction, as registered:{" "}
                  </span>
                  {hypothesis.prediction}
                </p>
                {spec ? (
                  <div className="space-y-2 border-l-2 border-border pl-3">
                    <ConditionList conditions={spec.hypothesis.conditions} />
                    <p className="text-xs text-muted-foreground">
                      Outcome: {describeLabel(spec.label)}. {directionNote(spec.hypothesis.direction)}
                    </p>
                  </div>
                ) : (
                  <p className="font-mono text-xs text-destructive">
                    Specification {hypothesis.experiment_id} is missing.
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-xs">
                  <Link href={`/oink-corp/experiments/${hypothesis.experiment_id}`} className="underline underline-offset-4">
                    Experiment {hypothesis.experiment_id}
                  </Link>
                  {comparison && (
                    <span className="text-muted-foreground">
                      compared with{" "}
                      <Link href={`/oink-corp/experiments/${comparison.reference}`} className="underline underline-offset-4">
                        {comparison.reference}
                      </Link>
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
                  <span className="text-muted-foreground">Evidence vs its reference:</span>
                  {Object.keys(latest).length === 0 ? (
                    <span className="text-muted-foreground">not tested yet</span>
                  ) : (
                    Object.entries(latest).map(([segment, run]) => (
                      <span key={segment} className="inline-flex items-center gap-1">
                        <VerdictBadge verdict={verdictOf(run)} failed={run.status !== "completed"} />
                        <span className="text-muted-foreground">{segmentLabel(segment)}</span>
                      </span>
                    ))
                  )}
                  {!latest.validation && <span className="text-muted-foreground">· not validated</span>}
                </div>
              </article>
            );
          })}
        </div>
        <p className="text-xs text-muted-foreground">
          Verdicts are directional — relative to the reference group, in the experiment&apos;s
          Long/Short direction. Read them against each registered prediction: where a hypothesis
          predicts only &quot;a difference&quot;, either sign would be informative, and neither is
          support until validated.
        </p>
      </Section>

      <Section title="What each hypothesis is compared against">
        <ul className="space-y-2 text-sm">
          {campaign.baselines.map((baseline) => (
            <li key={baseline.id}>
              <span className="font-mono text-xs font-semibold">{baseline.id}</span>
              {baseline.experiment_id && (
                <Link href={`/oink-corp/experiments/${baseline.experiment_id}`} className="ml-2 font-mono text-xs underline underline-offset-4">
                  {baseline.experiment_id}
                </Link>
              )}
              <p className="text-muted-foreground">{baseline.description}</p>
            </li>
          ))}
        </ul>
        <div className="rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">
          <p className="font-medium">Incremental comparison: does the extra condition add information?</p>
          <p className="text-muted-foreground">
            Each candidate selects a subset of its reference group (the reference&apos;s conditions
            plus more). At every timestamp the candidate&apos;s mean outcome is compared with the
            reference group&apos;s, on rows where both can be measured, and the Evaluator&apos;s
            rules decide whether that difference is evidence. A difference is not proof of
            predictive improvement: it can reflect one market episode, chance, or how the groups
            were drawn.
          </p>
        </div>
      </Section>

      <Section title="Results">
        <ResultsTable detail={detail} comparisonRuns={comparisonRuns} experimentRuns={experimentRuns} />
      </Section>

      <Section title="Recorded conclusion">
        {recorded.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {detail.report === null
              ? "No campaign report has been recorded."
              : "The campaign report has no Conclusion or Knowledge status section."}
          </p>
        ) : (
          recorded.map((section) => (
            <div key={section.title} className="space-y-2">
              <h4 className="font-mono text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {section.title}
              </h4>
              <ReportText markdown={section.body} />
            </div>
          ))
        )}
        {sections.length > 0 && (
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer font-mono text-xs">Full research report</summary>
            <div className="mt-4 space-y-6">
              {sections.map((section, n) => (
                <div key={n} className="space-y-2">
                  {section.title && <h4 className="font-semibold">{section.title}</h4>}
                  <ReportText markdown={section.body} />
                </div>
              ))}
            </div>
          </details>
        )}
      </Section>

      <Provenance detail={detail} />
    </div>
  );
}

function EvidenceStatus({ detail }: { detail: CampaignDetail }) {
  const comparisons = detail.campaign.comparisons;
  const latestDev: (Verdict | null)[] = [];
  let validated = 0;
  for (const c of comparisons) {
    const latest = latestBySegment(detail.comparison_runs[c.comparison_id] ?? []);
    if (latest.development) latestDev.push(verdictOf(latest.development));
    if (latest.validation) validated += 1;
  }
  if (latestDev.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Registered; none of its {comparisons.length} primary comparisons has been run.
      </p>
    );
  }
  const counts = VERDICTS.map((v) => [v, latestDev.filter((x) => x === v).length] as const).filter(
    ([, n]) => n > 0,
  );
  const only = counts.length === 1 ? counts[0][0] : null;
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground uppercase">Current evidence</span>
        {counts.map(([verdict, n]) => (
          <span key={verdict} className="inline-flex items-center gap-1">
            <VerdictBadge verdict={verdict} size="lg" />
            <span className="font-mono text-xs text-muted-foreground">× {n}</span>
          </span>
        ))}
      </div>
      <p className="text-sm">
        {only
          ? `${only === "insufficient_data" ? "All" : "Each of"} ${latestDev.length} executed primary comparison${latestDev.length === 1 ? "" : "s"}: ${VERDICT_META[only].label.toLowerCase()} (development).`
          : `${latestDev.length} of ${comparisons.length} primary comparisons executed (development); verdicts differ.`}{" "}
        {validated === 0
          ? "No independent validation has been run."
          : `${validated} validated.`}
      </p>
      {only === "insufficient_data" && (
        <p className="text-sm text-muted-foreground">{VERDICT_META.insufficient_data.meaning}</p>
      )}
    </div>
  );
}

function ResultsTable({
  detail,
  comparisonRuns,
  experimentRuns,
}: {
  detail: CampaignDetail;
  comparisonRuns: Record<string, RunRecord[]>;
  experimentRuns: Record<string, RunRecord[]>;
}) {
  const rows: { name: string; role: string; link: string; run: RunRecord }[] = [];
  for (const c of detail.campaign.comparisons) {
    for (const run of Object.values(latestBySegment(comparisonRuns[c.comparison_id] ?? []))) {
      rows.push({ name: c.comparison_id, role: `${c.hypothesis} vs reference`, link: c.candidate, run });
    }
  }
  for (const [id, runs] of Object.entries(experimentRuns)) {
    for (const run of Object.values(latestBySegment(runs))) {
      rows.push({ name: id, role: "vs eligible universe", link: id, run });
    }
  }
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No runs recorded for this campaign yet.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border">
              <th className="py-2 pr-3 font-medium">Run of</th>
              <th className="py-2 pr-3 font-medium">Compared</th>
              <th className="py-2 pr-3 font-medium">Segment</th>
              <th className="py-2 pr-3 font-medium">Verdict</th>
              <th className="py-2 pr-3 text-right font-medium">Timestamps</th>
              <th className="py-2 pr-3 text-right font-medium">Days</th>
              <th className="py-2 pr-3 text-right font-medium">Coins</th>
              <th className="py-2 pr-3 text-right font-medium">Spread</th>
              <th className="py-2 font-medium">Interval</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {rows.map(({ name, role, link, run }) => {
              const verdict = verdictOf(run);
              const figures = evidenceFigures(run);
              const evidence = verdict === "positive_evidence" || verdict === "negative_evidence";
              const interval = run.report?.uncertainty;
              return (
                <tr key={`${name}/${run.run_id}`}>
                  <td className="py-2 pr-3">
                    <Link href={`/oink-corp/experiments/${link}`} className="underline-offset-4 hover:underline">
                      {name}
                    </Link>
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">{role}</td>
                  <td className="py-2 pr-3">{run.segment}</td>
                  <td className="py-2 pr-3">
                    <VerdictBadge verdict={verdict} failed={run.status !== "completed"} />
                  </td>
                  <td className="py-2 pr-3 text-right">{formatCount(figures.timestampsWithSelection)}</td>
                  <td className="py-2 pr-3 text-right">{formatCount(figures.days)}</td>
                  <td className="py-2 pr-3 text-right">{formatCount(figures.symbols)}</td>
                  <td className={`py-2 pr-3 text-right ${evidence ? "" : "text-muted-foreground italic"}`}>
                    {formatSpread(figures.spread)}
                  </td>
                  <td className="py-2 text-muted-foreground">
                    {interval && !interval.suppressed && interval.interval
                      ? `[${formatSpread(interval.interval[0])}, ${formatSpread(interval.interval[1])}]`
                      : "not computed"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        Latest run per segment. Spreads in <span className="italic">italics</span> are descriptive
        observations from runs without an evidence verdict — not findings. &quot;Not
        computed&quot;: the evaluator suppresses intervals below its minimum number of days.
        Open an experiment for the full record and every run.
      </p>
    </div>
  );
}

function Provenance({ detail }: { detail: CampaignDetail }) {
  const { campaign } = detail;
  const allRuns = [
    ...Object.values(detail.comparison_runs).flat(),
    ...Object.values(detail.experiment_runs).flat(),
  ];
  const versions = new Set(
    allRuns.map((r) =>
      r.kind === "incremental_comparison"
        ? `comparison protocol v${r.comparison_version} (Evaluator v${r.evaluator_version} rules)`
        : `Evaluator v${r.evaluator_version}`,
    ),
  );
  const datasets = new Map<string, string>();
  for (const spec of Object.values(detail.specs)) {
    for (const d of spec?.datasets ?? []) datasets.set(d.name, d.data_sha256);
  }
  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer font-mono text-xs">Research provenance</summary>
      <dl className="mt-4 space-y-4 text-sm">
        <Item label="Campaign">{campaign.campaign_id}</Item>
        <Item label="Campaign hash (recorded by its comparison runs)">
          {detail.campaign_hashes.length === 0
            ? "no comparison run yet"
            : detail.campaign_hashes.map((h) => (
                <code key={h} className="block font-mono text-xs break-all">
                  {h}
                </code>
              ))}
          {detail.campaign_hashes.length > 1 && (
            <p className="text-destructive">
              Runs recorded different campaign hashes: the pre-registration changed between runs.
            </p>
          )}
        </Item>
        <Item label="Evaluation protocols used">
          {versions.size ? [...versions].join(" · ") : "none yet"}
        </Item>
        <Item label="Datasets (pinned by hash)">
          {[...datasets].map(([name, hash]) => (
            <code key={name} className="block font-mono text-xs break-all">
              {name} {hash}
            </code>
          ))}
        </Item>
        <Item label="Experiment specifications">
          {Object.keys(detail.specs).map((id) => (
            <Link key={id} href={`/oink-corp/experiments/${id}`} className="block font-mono text-xs underline-offset-4 hover:underline">
              {id}
            </Link>
          ))}
        </Item>
        <Item label="Parameters, fixed before outcomes were examined">
          <p className="leading-relaxed">{campaign.parameters_rationale}</p>
        </Item>
        <Item label="Evaluation plan">
          <p className="leading-relaxed">{campaign.evaluation_plan}</p>
        </Item>
        <Item label="Immutable runs">
          {allRuns.map((r) => (
            <code key={`${r.experiment_id}/${r.run_id}`} className="block font-mono text-xs">
              runs/{r.experiment_id}/{r.run_id}
            </code>
          ))}
        </Item>
        <Item label="Report">research/oink_corp/campaigns/{campaign.campaign_id}.report.md</Item>
      </dl>
    </details>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="font-mono text-xs text-muted-foreground uppercase">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
      {children}
    </section>
  );
}
