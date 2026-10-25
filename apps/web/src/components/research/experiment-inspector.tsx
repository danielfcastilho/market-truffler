import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { ConditionList } from "@/components/research/condition-list";
import { DirectionTag, KnowledgeBadge } from "@/components/research/knowledge-badge";
import { RunEvidence } from "@/components/research/run-evidence";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  KNOWLEDGE_META,
  VALIDATION_META,
  describeLabel,
  directionNote,
  hypothesisTitle,
  isKnowledgeStatus,
  formatTimestamp,
  latestBySegment,
  segmentLabel,
  shortHash,
  verdictOf,
  type ExperimentDetail,
  type RunRecord,
} from "@/lib/research";

/**
 * One experiment from hypothesis to evaluation: its definition, the latest
 * recorded evaluation per segment, the incremental comparisons it takes
 * part in, and every immutable run — listed individually, never merged.
 */
export function ExperimentInspector({ detail }: { detail: ExperimentDetail }) {
  const { spec } = detail;
  const latest = latestBySegment(detail.runs);

  return (
    <div className="max-w-4xl space-y-10">
      <ArtifactIssues issues={detail.issues} />

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {spec && <DirectionTag direction={spec.hypothesis.direction} />}
          <h2 className="text-lg font-semibold tracking-tight">
            {spec ? hypothesisTitle(spec.hypothesis.conditions) : detail.experiment_id}
          </h2>
        </div>
        {spec && (
          <p className="font-mono text-xs break-all text-muted-foreground">
            Experiment {detail.experiment_id}
          </p>
        )}
        {detail.campaigns.map((c) => (
          <p key={c.campaign_id} className="font-mono text-xs text-muted-foreground">
            In{" "}
            <Link href={`/oink-corp/campaigns/${c.campaign_id}`} className="underline underline-offset-4">
              {c.campaign_id}
            </Link>{" "}
            as {c.roles.join(", ")}
          </p>
        ))}
      </header>

      {detail.knowledge.map((k) => (
        <div
          key={`${k.campaign_id}/${k.hypothesis_id}`}
          className="space-y-2 rounded-2xl bg-card p-5 ring-1 ring-foreground/10"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm">
              Tests hypothesis{" "}
              <Link
                href={`/oink-corp/campaigns/${k.campaign_id}#${k.hypothesis_id}`}
                className="font-medium underline underline-offset-4"
              >
                {k.hypothesis_id}
              </Link>{" "}
              of {k.campaign_id}
            </p>
            <KnowledgeBadge status={k.knowledge_status} size="lg" />
          </div>
          <p className="text-sm text-muted-foreground">
            {isKnowledgeStatus(k.knowledge_status) && KNOWLEDGE_META[k.knowledge_status].meaning}{" "}
            {VALIDATION_META[k.validation_status]}. (Knowledge ledger revision {k.revision}.)
          </p>
        </div>
      ))}

      <Section title="Definition">
        {spec === null ? (
          <p className="text-sm text-destructive">
            No specification file exists for this id; only its run records are shown.
          </p>
        ) : (
          <div className="space-y-4 text-sm">
            <p className="leading-relaxed">{spec.hypothesis.statement}</p>
            <p className="text-muted-foreground">
              <span className="font-mono text-xs uppercase">Intuition behind it: </span>
              {spec.heuristic.text}
            </p>
            <div className="space-y-1">
              <p className="font-mono text-xs text-muted-foreground uppercase">
                Selects, at each timestamp, coins where
              </p>
              <ConditionList conditions={spec.hypothesis.conditions} />
            </div>
            <dl className="grid gap-3 sm:grid-cols-2">
              <Item label="Direction">
                <span className="capitalize">{spec.hypothesis.direction}</span> —{" "}
                {directionNote(spec.hypothesis.direction)}
              </Item>
              <Item label="Outcome measured">
                {describeLabel(spec.label)}{" "}
                <code className="font-mono text-xs text-muted-foreground">{spec.label}</code>
              </Item>
            </dl>
            <Item label="Datasets (pinned by content hash)">
              {spec.datasets.map((d) => (
                <code key={d.name} className="block font-mono text-xs break-all">
                  {d.name} {d.data_sha256}
                </code>
              ))}
            </Item>
            <details className="text-xs">
              <summary className="cursor-pointer font-mono text-muted-foreground">
                Technical specification (JSON)
              </summary>
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono">
                {JSON.stringify(spec, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </Section>

      <Section
        title={
          detail.runs[0]?.kind === "incremental_comparison"
            ? "Evaluation (incremental comparison)"
            : "Evaluation against the eligible universe"
        }
      >
        {detail.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">Registered but never run — no evidence exists.</p>
        ) : (
          <div className="space-y-6">
            {Object.entries(latest).map(([segment, run]) => (
              <div key={segment} className="rounded-xl p-4 ring-1 ring-foreground/10">
                <RunEvidence record={run} />
              </div>
            ))}
            {!latest.validation && (
              <p className="text-sm text-muted-foreground">
                Not validated: no validation run exists, so nothing here is independently
                confirmed.
              </p>
            )}
          </div>
        )}
      </Section>

      {detail.comparisons.length > 0 && (
        <Section title="Incremental comparisons">
          <p className="text-sm text-muted-foreground">
            A candidate adds conditions to a reference; its spread says how the candidate&apos;s
            subset fared against the whole reference group on comparable rows. A difference is not
            automatically proof of predictive improvement.
          </p>
          {detail.comparisons.map(({ comparison, role, runs, campaign_id }) => {
            const other = role === "candidate" ? comparison.reference : comparison.candidate;
            const latestRuns = latestBySegment(runs);
            return (
              <div key={comparison.comparison_id} className="space-y-3 rounded-xl p-4 ring-1 ring-foreground/10">
                <div className="font-mono text-xs">
                  <p className="font-semibold">{comparison.comparison_id}</p>
                  <p className="text-muted-foreground">
                    {comparison.hypothesis} in{" "}
                    <Link href={`/oink-corp/campaigns/${campaign_id}`} className="underline underline-offset-4">
                      {campaign_id}
                    </Link>{" "}
                    · this experiment is the {role}; {role === "candidate" ? "reference" : "candidate"}:{" "}
                    <Link href={`/oink-corp/experiments/${other}`} className="underline underline-offset-4">
                      {other}
                    </Link>
                  </p>
                </div>
                {runs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Not run yet.</p>
                ) : (
                  Object.entries(latestRuns).map(([segment, run]) => (
                    <RunEvidence key={segment} record={run} />
                  ))
                )}
              </div>
            );
          })}
        </Section>
      )}

      <Section title={`Run history (${detail.runs.length})`}>
        {detail.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No runs.</p>
        ) : (
          <RunHistory runs={detail.runs} />
        )}
      </Section>
    </div>
  );
}

function RunHistory({ runs }: { runs: RunRecord[] }) {
  return (
    <div className="space-y-2">
      {runs.map((run) => (
        <details key={run.run_id} className="rounded-lg border border-border p-3">
          <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs">
            <span>{run.run_id}</span>
            <span className="text-muted-foreground">{formatTimestamp(run.executed_at)}</span>
            <span>{segmentLabel(run.segment)}</span>
            <VerdictBadge verdict={verdictOf(run)} failed={run.status !== "completed"} />
          </summary>
          <dl className="mt-3 grid gap-2 font-mono text-xs sm:grid-cols-2">
            <Item label="Protocol">
              {run.kind === "incremental_comparison"
                ? `Comparison v${run.comparison_version}`
                : `Evaluator v${run.evaluator_version}`}
            </Item>
            <Item label="Spec hash">{shortHash(run.spec_hash)}</Item>
            <Item label="Hypothesis hash">{shortHash(run.hypothesis_hash)}</Item>
            <Item label="Campaign hash">{shortHash(run.campaign_hash)}</Item>
            <Item label="Datasets">
              {(run.datasets ?? []).map((d) => `${d.name} ${shortHash(d.data_sha256)}`).join(", ") || "—"}
            </Item>
            <Item label="Code">
              {run.git_commit ?? "—"} · fingerprint {shortHash(run.code_fingerprint)}
            </Item>
          </dl>
          <div className="mt-3">
            <RunEvidence record={run} />
          </div>
          <details className="mt-3 text-xs">
            <summary className="cursor-pointer font-mono text-muted-foreground">Raw record (JSON)</summary>
            <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono">
              {JSON.stringify(run, null, 2)}
            </pre>
          </details>
        </details>
      ))}
    </div>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="font-mono text-xs text-muted-foreground uppercase">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
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
