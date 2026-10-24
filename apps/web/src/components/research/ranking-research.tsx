import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag } from "@/components/research/knowledge-badge";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  VERDICT_META,
  describeLabel,
  describeTerm,
  formatCount,
  formatReturn,
  formatSpread,
  formatTimestamp,
  shortHash,
  type RankingCandidate,
  type RankingModelSummary,
  type RankingResearch as Research,
  type RankingRun,
  type RankingStudyView,
} from "@/lib/research";

/**
 * Ranking research: do explicitly specified scoring models put better
 * opportunities at the top? Model-level, development-only evidence,
 * kept apart from the hypothesis (feature-level) research. Every number is
 * a recorded run's; verdicts are the ranking evaluator's.
 */
export function RankingResearch({ research }: { research: Research }) {
  return (
    <div className="max-w-5xl space-y-10">
      <ArtifactIssues issues={research.issues} />
      <div className="space-y-3 text-sm leading-relaxed">
        <p className="max-w-3xl text-muted-foreground">
          <span className="font-medium text-foreground">Feature research</span> asks whether a
          market condition tells us something about what happens next.{" "}
          <span className="font-medium text-foreground">Ranking research</span> asks whether a
          scoring model built from such features ranks coins so that its top picks do better than
          the rest of the market — and better than a simpler model.
        </p>
        <div role="note" className="rounded-xl border border-amber-500/50 bg-amber-500/10 p-4">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            Development research only
          </p>
          <p className="text-amber-900/80 dark:text-amber-200/80">
            Candidate weights are chosen by hand; nothing has been optimized. Every result comes
            from development data and is exploratory: no candidate is validated, no Champion model
            exists, and none of this is a live ranking or a trading signal.
          </p>
        </div>
      </div>

      {research.studies.length === 0 ? (
        <p className="text-sm text-muted-foreground">No ranking study has been registered yet.</p>
      ) : (
        research.studies.map((view) => <Study key={view.study.study_id} view={view} />)
      )}
    </div>
  );
}

function Study({ view }: { view: RankingStudyView }) {
  const { study, candidate, reference } = view;
  const latest = view.runs.find((r) => r.status === "completed") ?? view.runs[0] ?? null;
  return (
    <article
      id={study.study_id}
      className="scroll-mt-6 space-y-6 rounded-2xl bg-card p-5 ring-1 ring-foreground/10 md:p-6"
    >
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <DirectionTag direction={candidate?.direction} />
          <span className="font-mono text-xs text-muted-foreground">{study.study_id}</span>
        </div>
        <h2 className="text-lg font-semibold tracking-tight">{study.name}</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">{study.question}</p>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <ModelCard role="Reference" model={reference} />
        <ModelCard role="Candidate" model={candidate} />
      </div>

      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <Item label="Top K">{study.top_k} coins</Item>
        <Item label="Outcome">{describeLabel(study.label)}</Item>
        <Item label="Primary metric (registered)">
          Top-K excess utility: the top {study.top_k}&apos;s mean next-hour{" "}
          {candidate?.direction ?? ""} utility minus the eligible universe&apos;s, at the same
          timestamp{reference ? "; candidate minus reference on identical coins and timestamps" : ""}.
        </Item>
      </dl>

      {latest === null ? (
        <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          Registered but never run — no evidence exists.
        </p>
      ) : (
        <Evidence run={latest} hasReference={reference !== null} />
      )}

      {view.runs.length > 0 && <RunHistory runs={view.runs} />}
    </article>
  );
}

function ModelCard({ role, model }: { role: string; model: RankingCandidate | null }) {
  return (
    <div className="space-y-2 rounded-xl border border-border p-4">
      <p className="font-mono text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
        {role}
      </p>
      {model === null ? (
        <p className="text-sm text-destructive">Specification missing.</p>
      ) : (
        <>
          <p className="font-medium">{model.name}</p>
          <ul className="space-y-1 text-sm">
            {model.terms.map((term) => (
              <li key={term.feature}>
                {describeTerm(term)}
                <code className="ml-2 font-mono text-xs text-muted-foreground">
                  {term.weight} × {term.transform}({term.feature})
                </code>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Higher score = more attractive {model.direction}. Weights set by hand ·{" "}
            {model.model_family} · coins missing any feature are excluded.
          </p>
        </>
      )}
    </div>
  );
}

function Evidence({ run, hasReference }: { run: RankingRun; hasReference: boolean }) {
  if (run.status !== "completed" || !run.report) {
    return (
      <div className="space-y-2">
        <VerdictBadge verdict={null} failed size="lg" />
        <p className="font-mono text-xs text-destructive">
          No evaluation was produced: {run.error ?? "unknown error"}
        </p>
      </div>
    );
  }
  const report = run.report;
  const primary = report.primary;
  const evidence =
    primary.verdict === "positive_evidence" || primary.verdict === "negative_evidence";
  const u = primary.uncertainty;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={primary.verdict} size="lg" />
        <span className="font-mono text-xs text-muted-foreground">
          Development (exploratory) · {formatTimestamp(run.executed_at)}
        </span>
      </div>
      <p className="text-muted-foreground">{VERDICT_META[primary.verdict].meaning}</p>
      {primary.verdict_reasons.length > 0 && (
        <div>
          <p className="mb-1 font-mono text-xs text-muted-foreground uppercase">
            {primary.verdict === "insufficient_data"
              ? "Why no conclusion is possible (minimums required)"
              : "Criteria not met"}
          </p>
          <ul className="list-disc space-y-0.5 pl-5 font-mono text-xs">
            {primary.verdict_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-3 font-mono text-xs sm:grid-cols-4">
        <Item label="Timestamps evaluated">
          {formatCount(hasReference ? (primary.matched_timestamps ?? null) : report.candidate.timestamps)}
        </Item>
        <Item label="Days">{formatCount(report.coverage.distinct_days)}</Item>
        <Item label="Mean eligible coins">
          {report.coverage.mean_universe === null ? "—" : Math.round(report.coverage.mean_universe)}
        </Item>
        <Item label="Skipped (universe too small)">
          {formatCount(report.coverage.timestamps_universe_too_small)}
        </Item>
      </dl>
      <div className={evidence ? "" : "rounded-lg border border-dashed border-border p-3"}>
        {!evidence && (
          <p className="mb-2 font-mono text-xs text-muted-foreground uppercase">
            Descriptive observations — not evidence
          </p>
        )}
        <dl className="grid gap-3 font-mono text-xs sm:grid-cols-3">
          {report.reference && (
            <Item label="Reference: top-K excess utility">
              {formatSpread(report.reference.mean_top_k_excess_utility)}
            </Item>
          )}
          <Item label="Candidate: top-K excess utility">
            {formatSpread(report.candidate.mean_top_k_excess_utility)}
          </Item>
          {hasReference && (
            <Item label="Paired difference (candidate − reference)">
              {formatSpread(primary.mean)}
            </Item>
          )}
        </dl>
      </div>
      <p className="font-mono text-xs">
        {!u.suppressed && u.interval ? (
          <>
            {Math.round((u.confidence_level ?? 0) * 100)}% interval: [{formatSpread(u.interval[0])},{" "}
            {formatSpread(u.interval[1])}] — <span className="text-muted-foreground">{u.method}</span>
          </>
        ) : (
          <span className="text-muted-foreground">No uncertainty interval: {u.reason}.</span>
        )}
      </p>
      <details className="text-xs">
        <summary className="cursor-pointer font-mono text-muted-foreground">
          Exploratory diagnostics (never decide a result)
        </summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {report.reference && <Diagnostics label="Reference" summary={report.reference} />}
          <Diagnostics label="Candidate" summary={report.candidate} />
        </div>
      </details>
      <details className="text-xs">
        <summary className="cursor-pointer font-mono text-muted-foreground">
          Standing limitations ({report.disclosures.length})
        </summary>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
          {report.disclosures.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Diagnostics({ label, summary }: { label: string; summary: RankingModelSummary }) {
  const d = summary.diagnostics;
  return (
    <dl className="space-y-1 font-mono">
      <dt className="font-semibold">{label}</dt>
      <dd>Top-K mean utility: {formatReturn(d.mean_top_k_utility)}</dd>
      <dd>Top-minus-bottom-K separation: {formatSpread(d.mean_top_bottom_separation)}</dd>
      <dd>
        Mean Spearman (score vs utility):{" "}
        {d.mean_spearman === null ? "—" : d.mean_spearman.toFixed(3)}
      </dd>
      <dd>
        {summary.distinct_top_symbols} distinct coins in top K · {summary.top_k_missing_outcome}{" "}
        top-K picks without an outcome (excluded)
      </dd>
    </dl>
  );
}

function RunHistory({ runs }: { runs: RankingRun[] }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer font-mono text-xs text-muted-foreground">
        Runs and provenance ({runs.length})
      </summary>
      <ul className="mt-3 space-y-3 font-mono text-xs">
        {runs.map((run) => (
          <li key={run.run_id} className="space-y-0.5 rounded-lg border border-border p-3">
            <p className="flex flex-wrap items-center gap-2">
              <span>{run.run_id}</span>
              <VerdictBadge verdict={run.report?.verdict ?? null} failed={run.status !== "completed"} />
              {run.repeat_of.length > 0 && (
                <span className="text-muted-foreground">
                  repeat of {run.repeat_of.length} earlier run(s) — not new evidence
                </span>
              )}
            </p>
            <p className="text-muted-foreground">
              {formatTimestamp(run.executed_at)} · ranking evaluator v{run.ranking_evaluator_version}{" "}
              · study {shortHash(run.spec_hash)} · candidate {run.candidate.candidate_id}{" "}
              {shortHash(run.candidate.spec_hash)}
              {run.reference && ` · reference ${run.reference.candidate_id} ${shortHash(run.reference.spec_hash)}`}
            </p>
            <p className="text-muted-foreground">
              datasets: {(run.datasets ?? []).map((d) => `${d.name} ${shortHash(d.data_sha256)}`).join(", ") || "—"}
            </p>
          </li>
        ))}
      </ul>
    </details>
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
