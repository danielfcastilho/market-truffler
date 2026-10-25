import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { ConditionList } from "@/components/research/condition-list";
import { IndependentTesting } from "@/components/research/independent-testing";
import { DirectionTag, KnowledgeBadge } from "@/components/research/knowledge-badge";
import { ReportText } from "@/components/research/report-text";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  EVIDENCE_CLASS_LABEL,
  KNOWLEDGE_META,
  VALIDATION_META,
  describeLabel,
  directionNote,
  evidenceFigures,
  formatCount,
  formatSpread,
  formatTimestamp,
  hypothesisTitle,
  isKnowledgeStatus,
  latestBySegment,
  predictionKind,
  segmentLabel,
  shortHash,
  unseenDataAnswer,
  verdictOf,
  type CampaignDetail,
  type CampaignHypothesis,
  type KnowledgeStatus,
  type LedgerHypothesis,
  type LedgerRevision,
  type RunRecord,
} from "@/lib/research";

/**
 * One pre-registered campaign, hypothesis first: what each hypothesis
 * claims, what was predicted, what the knowledge ledger says the evidence
 * justifies, and the immutable runs behind it. Statuses come from the
 * ledger, verdicts from runs, the conclusion from the campaign report —
 * this page computes none of them.
 */
export function CampaignExplorer({ detail }: { detail: CampaignDetail }) {
  const { campaign } = detail;
  const ledger = detail.knowledge_revisions.at(-1) ?? null;
  const entries = new Map(ledger?.hypotheses.map((h) => [h.hypothesis_id, h]) ?? []);
  const sections = detail.report ?? [];
  const recorded = sections.filter((s) => s.title === "Conclusion");
  const directionOf = (h: CampaignHypothesis) =>
    detail.specs[h.experiment_id]?.hypothesis.direction ?? "unknown";
  const groups = (["long", "short", "unknown"] as const).map((direction) => ({
    direction,
    hypotheses: campaign.hypotheses.filter((h) => directionOf(h) === direction),
  }));

  return (
    <div className="max-w-4xl space-y-12">
      <ArtifactIssues issues={detail.issues} />

      <header className="space-y-4">
        <p className="font-mono text-xs text-muted-foreground">
          Campaign {campaign.campaign_id} · registered {campaign.created}
        </p>
        <h2 className="text-2xl leading-snug font-semibold tracking-tight">{campaign.question}</h2>
        <KnowledgeSummary detail={detail} ledger={ledger} />
        <p className="text-sm leading-relaxed text-muted-foreground">{campaign.motivation}</p>
      </header>

      <section className="space-y-3">
        <SectionTitle>Recorded conclusion</SectionTitle>
        {recorded.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {detail.report === null
              ? "No campaign report has been recorded."
              : "The campaign report has no Conclusion section."}
          </p>
        ) : (
          recorded.map((section) => <ReportText key={section.title} markdown={section.body} />)
        )}
        {sections.length > 0 && (
          <details className="rounded-xl border border-border p-4">
            <summary className="cursor-pointer text-sm font-medium">Full research report</summary>
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
      </section>

      {groups
        .filter((g) => g.hypotheses.length > 0)
        .map(({ direction, hypotheses }) => (
          <section key={direction} className="space-y-4">
            <SectionTitle>
              {direction === "unknown" ? "Hypotheses without a readable specification" : `${direction} hypotheses`}
            </SectionTitle>
            {hypotheses.map((hypothesis) => (
              <HypothesisCard
                key={hypothesis.id}
                detail={detail}
                hypothesis={hypothesis}
                entry={entries.get(hypothesis.id) ?? null}
                ledger={ledger}
              />
            ))}
          </section>
        ))}

      <section className="space-y-3">
        <SectionTitle>Independent testing on unseen data</SectionTitle>
        <IndependentTesting evaluations={detail.evaluations} />
      </section>

      <section className="space-y-3">
        <SectionTitle>What each hypothesis is compared against</SectionTitle>
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
        <div className="rounded-xl bg-muted/50 p-4 text-sm leading-relaxed">
          <p className="font-medium">Incremental comparison: does the extra condition add information?</p>
          <p className="text-muted-foreground">
            Each candidate selects a subset of its reference group (the reference&apos;s conditions
            plus more). At every timestamp the candidate&apos;s mean outcome is compared with the
            reference group&apos;s, on rows where both can be measured, and the Evaluator&apos;s
            rules decide whether that difference is evidence. A difference is not proof of
            predictive improvement — and feature-level evidence says nothing yet about whether a
            ranking model using the feature would rank better.
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <SectionTitle>Experimental evidence (latest run per segment)</SectionTitle>
        <ResultsTable detail={detail} />
      </section>


      <KnowledgeHistory detail={detail} />
      <Provenance detail={detail} />
    </div>
  );
}

function KnowledgeSummary({
  detail,
  ledger,
}: {
  detail: CampaignDetail;
  ledger: LedgerRevision | null;
}) {
  if (!ledger) {
    return (
      <p className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
        Knowledge has not been derived for this campaign yet — its hypotheses have no recorded
        knowledge status.
      </p>
    );
  }
  const counts = new Map<string, number>();
  for (const h of ledger.hypotheses) counts.set(h.knowledge_status, (counts.get(h.knowledge_status) ?? 0) + 1);
  const validated = ledger.hypotheses.filter((h) => h.validation_status === "independently_validated").length;
  const [only] = counts.size === 1 ? [...counts.keys()] : [null];
  return (
    <div className="space-y-2 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
          What is known
        </span>
        {[...counts].map(([status, n]) => (
          <span key={status} className="inline-flex items-center gap-1">
            <KnowledgeBadge status={status} size="lg" />
            <span className="font-mono text-xs text-muted-foreground">× {n}</span>
          </span>
        ))}
      </div>
      <p className="text-sm">
        {only && isKnowledgeStatus(only)
          ? `All ${ledger.hypotheses.length} hypotheses: ${KNOWLEDGE_META[only].label.toLowerCase()}. ${KNOWLEDGE_META[only].meaning}`
          : `${ledger.hypotheses.length} hypotheses with differing knowledge states.`}{" "}
        {validated === 0
          ? "No independent validation has been run."
          : `${validated} independently validated.`}
      </p>
      <LedgerCheck detail={detail} />
    </div>
  );
}

function LedgerCheck({ detail }: { detail: CampaignDetail }) {
  const check = detail.knowledge_check;
  if (!check || (check.problems.length === 0 && check.uncited_runs.length === 0)) return null;
  return (
    <div role="alert" className="space-y-1 text-sm">
      {check.problems.length > 0 && (
        <p className="font-medium text-destructive">
          The knowledge ledger cites runs that do not match their records — do not rely on it:{" "}
          {check.problems.join("; ")}
        </p>
      )}
      {check.uncited_runs.length > 0 && (
        <p className="text-amber-800 dark:text-amber-300">
          Newer runs exist that the latest ledger revision does not include yet (
          {check.uncited_runs.length}); its statuses may be out of date.
        </p>
      )}
    </div>
  );
}

function HypothesisCard({
  detail,
  hypothesis,
  entry,
  ledger,
}: {
  detail: CampaignDetail;
  hypothesis: CampaignHypothesis;
  entry: LedgerHypothesis | null;
  ledger: LedgerRevision | null;
}) {
  const spec = detail.specs[hypothesis.experiment_id];
  const status = entry?.knowledge_status;
  const primary = entry?.evidence.filter((e) => e.role === "primary") ?? [];
  const context = entry?.evidence.filter((e) => e.role === "context" && e.current) ?? [];
  const unseen = unseenDataAnswer(hypothesis.id, entry, detail.evaluations);
  return (
    <article id={hypothesis.id} className="scroll-mt-6 space-y-5 rounded-2xl bg-card p-5 ring-1 ring-foreground/10 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <DirectionTag direction={spec?.hypothesis.direction} />
            <span className="font-mono text-xs text-muted-foreground">{hypothesis.id}</span>
          </div>
          <h3 className="text-lg font-semibold tracking-tight">
            {spec ? hypothesisTitle(spec.hypothesis.conditions) : hypothesis.experiment_id}
          </h3>
        </div>
        <KnowledgeBadge status={status} size="lg" />
      </header>

      <Field label="Hypothesis">
        <p className="leading-relaxed">{hypothesis.statement}</p>
        {spec && (
          <div className="mt-2 space-y-1">
            <ConditionList conditions={spec.hypothesis.conditions} />
            <p className="text-xs text-muted-foreground">
              Outcome: {describeLabel(spec.label)}. {directionNote(spec.hypothesis.direction)}
            </p>
          </div>
        )}
      </Field>

      <Field label="Prediction (registered before evaluation)">
        <p>{hypothesis.prediction}</p>
        {entry && <p className="mt-1 text-xs text-muted-foreground">{predictionKind(entry.prediction_sign)}</p>}
      </Field>

      <Field label="Knowledge status">
        {entry && isKnowledgeStatus(status) ? (
          <p>{KNOWLEDGE_META[status as KnowledgeStatus].meaning}</p>
        ) : (
          <p className="text-muted-foreground">Not yet in the knowledge ledger.</p>
        )}
      </Field>

      <div className="grid gap-5 md:grid-cols-2">
        <Field label="Evidence">
          {primary.length === 0 ? (
            <p className="text-muted-foreground">No evaluation cited yet.</p>
          ) : (
            <ul className="space-y-1.5">
              {primary
                .filter((e) => e.current)
                .map((e) => (
                  <li key={e.run_id} className="flex flex-wrap items-center gap-2">
                    <VerdictBadge verdict={e.verdict} />
                    <span className="text-xs text-muted-foreground">
                      {e.evidence_class ? EVIDENCE_CLASS_LABEL[e.evidence_class] : segmentLabel(e.segment)} · vs reference
                    </span>
                  </li>
                ))}
              {context.map((e) => (
                <li key={e.run_id} className="flex flex-wrap items-center gap-2">
                  <VerdictBadge verdict={e.verdict} />
                  <span className="text-xs text-muted-foreground">
                    {segmentLabel(e.segment)} · vs eligible universe (context only)
                  </span>
                </li>
              ))}
              {primary.some((e) => !e.current) && (
                <li className="text-xs text-muted-foreground">
                  {primary.filter((e) => !e.current).length} earlier repeat run(s) on the same data —
                  not independent evidence.
                </li>
              )}
            </ul>
          )}
        </Field>
        <Field label="Validation">
          <p>{entry ? (VALIDATION_META[entry.validation_status] ?? entry.validation_status) : "—"}</p>
        </Field>
      </div>

      <div
        className={
          unseen.tested
            ? "rounded-lg bg-primary/10 p-3 text-sm"
            : "rounded-lg border border-dashed border-border p-3 text-sm"
        }
      >
        <p className="font-mono text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
          Independently tested on genuinely unseen data?
        </p>
        <p>{unseen.text}</p>
      </div>

      {entry && entry.limitations.length > 0 && (
        <Field label="What prevents a stronger conclusion">
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {entry.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Field>
      )}

      <details className="text-sm">
        <summary className="cursor-pointer font-mono text-xs text-muted-foreground">
          Technical provenance
        </summary>
        <dl className="mt-3 grid gap-3 font-mono text-xs sm:grid-cols-2">
          <Item label="Experiment">
            <Link href={`/oink-corp/experiments/${hypothesis.experiment_id}`} className="underline underline-offset-4 break-all">
              {hypothesis.experiment_id}
            </Link>
          </Item>
          <Item label="Primary evidence">{entry?.primary_evidence_ids.join(", ") ?? "—"}</Item>
          <Item label="Cited runs">
            {(entry?.evidence ?? []).map((e) => (
              <span key={e.run_id} className="block break-all">
                {e.experiment_id}/{e.run_id} · {e.protocol} · spec {shortHash(e.spec_hash)}
              </span>
            ))}
          </Item>
          <Item label="Datasets">
            {[...new Set((entry?.evidence ?? []).flatMap((e) => e.dataset_sha256 ?? []))]
              .map((h) => shortHash(h))
              .join(", ") || "—"}
          </Item>
          <Item label="Knowledge ledger">
            {ledger ? `revision ${ledger.revision} · ${shortHash(ledger.content_hash)}` : "—"}
          </Item>
        </dl>
      </details>
    </article>
  );
}

function ResultsTable({ detail }: { detail: CampaignDetail }) {
  const rows: { name: string; role: string; link: string; run: RunRecord }[] = [];
  for (const c of detail.campaign.comparisons) {
    for (const run of Object.values(latestBySegment(detail.comparison_runs[c.comparison_id] ?? []))) {
      rows.push({ name: c.comparison_id, role: `${c.hypothesis} vs reference`, link: c.candidate, run });
    }
  }
  for (const [id, runs] of Object.entries(detail.experiment_runs)) {
    for (const run of Object.values(latestBySegment(runs))) {
      rows.push({ name: id, role: "vs eligible universe", link: id, run });
    }
  }
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No runs recorded for this campaign yet.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
        <table className="w-full text-left font-mono text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Run of</th>
              <th className="px-3 py-2 font-medium">Compared</th>
              <th className="px-3 py-2 font-medium">Segment</th>
              <th className="px-3 py-2 font-medium">Verdict</th>
              <th className="px-3 py-2 text-right font-medium">Timestamps</th>
              <th className="px-3 py-2 text-right font-medium">Days</th>
              <th className="px-3 py-2 text-right font-medium">Coins</th>
              <th className="px-3 py-2 text-right font-medium">Spread</th>
              <th className="px-3 py-2 font-medium">Interval</th>
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
                  <td className="px-3 py-2">
                    <Link href={`/oink-corp/experiments/${link}`} className="underline-offset-4 hover:underline">
                      {name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{role}</td>
                  <td className="px-3 py-2">{run.segment}</td>
                  <td className="px-3 py-2">
                    <VerdictBadge verdict={verdict} failed={run.status !== "completed"} />
                  </td>
                  <td className="px-3 py-2 text-right">{formatCount(figures.timestampsWithSelection)}</td>
                  <td className="px-3 py-2 text-right">{formatCount(figures.days)}</td>
                  <td className="px-3 py-2 text-right">{formatCount(figures.symbols)}</td>
                  <td className={`px-3 py-2 text-right ${evidence ? "" : "text-muted-foreground italic"}`}>
                    {formatSpread(figures.spread)}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
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
        Spreads in <span className="italic">italics</span> are descriptive observations from runs
        without an evidence verdict — not findings. &quot;Not computed&quot;: the evaluator
        suppresses intervals below its minimum number of days.
      </p>
    </div>
  );
}

function KnowledgeHistory({ detail }: { detail: CampaignDetail }) {
  const revisions = detail.knowledge_revisions;
  return (
    <section className="space-y-3">
      <SectionTitle>Knowledge history</SectionTitle>
      {revisions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No knowledge ledger revision exists yet.</p>
      ) : (
        <ol className="space-y-3">
          {[...revisions].reverse().map((revision, n, all) => {
            const previous = all[n + 1];
            const before = new Map(previous?.hypotheses.map((h) => [h.hypothesis_id, h.knowledge_status]) ?? []);
            const changes = revision.hypotheses.filter((h) => before.get(h.hypothesis_id) !== h.knowledge_status);
            return (
              <li key={revision.revision} className="rounded-xl border border-border p-4 text-sm">
                <p className="font-medium">
                  Revision {revision.revision}{" "}
                  <span className="font-mono text-xs font-normal text-muted-foreground">
                    {formatTimestamp(revision.derived_at)} · rules v{revision.knowledge_rules_version} ·{" "}
                    {shortHash(revision.content_hash)}
                  </span>
                </p>
                {changes.length === 0 ? (
                  <p className="text-muted-foreground">New evidence cited; no status changed.</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {changes.map((h) => (
                      <li key={h.hypothesis_id} className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs">{h.hypothesis_id}</span>
                        <span className="text-xs text-muted-foreground">
                          {before.get(h.hypothesis_id) ? "changed to" : "first recorded as"}
                        </span>
                        <KnowledgeBadge status={h.knowledge_status} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}
      <p className="text-xs text-muted-foreground">
        Revisions are immutable and hash-chained; each one is re-derived from the runs it cites by{" "}
        <code className="font-mono">app.scripts.knowledge_ledger --verify</code>. Earlier states
        are not reconstructed: history starts with the first revision.
      </p>
    </section>
  );
}

function Provenance({ detail }: { detail: CampaignDetail }) {
  const { campaign } = detail;
  const ledger = detail.knowledge_revisions.at(-1);
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
    <details className="rounded-xl border border-border p-4">
      <summary className="cursor-pointer text-sm font-medium">Research provenance</summary>
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
        <Item label="Knowledge ledger">
          {ledger ? (
            <>
              revision {ledger.revision}, campaign hash{" "}
              <code className="font-mono text-xs break-all">{ledger.campaign_hash}</code>
              {ledger.scope_note && <p className="mt-1 text-muted-foreground">{ledger.scope_note}</p>}
            </>
          ) : (
            "none"
          )}
        </Item>
        <Item label="Evaluation protocols used">{versions.size ? [...versions].join(" · ") : "none yet"}</Item>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1 text-sm">
      <p className="font-mono text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      {children}
    </div>
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

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-mono text-xs font-semibold tracking-widest text-muted-foreground uppercase">
      {children}
    </h3>
  );
}
