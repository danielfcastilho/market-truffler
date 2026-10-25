import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag } from "@/components/research/knowledge-badge";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  VERDICT_META,
  describeCondition,
  describeLabel,
  describeTerm,
  formatCount,
  formatReturn,
  formatSpread,
  formatTimestamp,
  lifecycleLabel,
  modelStatusLabel,
  shortHash,
  type Condition,
  type Direction,
  type ModelKnowledge,
  type ModelKnowledgeEntry,
  type RankingCandidate,
  type RankingModelSummary,
  type RankingResearch,
  type RankingRun,
  type RankingStudyView,
  type TruffleRegistrationView,
} from "@/lib/research";
import {
  describeBlocker,
  latestRankingRun,
  liveState,
  parseBlocker,
  type LiveModels,
  type LiveState,
} from "@/lib/research-questions";
import { cn } from "@/lib/utils";

type Loaded<T> = { status: "ok"; data: T } | { status: "unavailable"; message: string };

const UNVALIDATED = new Set([
  "experimental",
  "registered_for_prospective_evaluation",
  "insufficient_prospective_evidence",
  "inconclusive_prospective_evidence",
]);

interface ModelView {
  id: string;
  name: string;
  direction: Direction | null;
  candidate: RankingCandidate | null;
  ledger: ModelKnowledgeEntry | null;
  specHash: string | null;
  live: LiveState;
  asCandidate: RankingStudyView[];
  asReference: RankingStudyView[];
}

/** A model's specification hash as recorded by its ledger, else by the
 * latest study run that evaluated it — never guessed. */
function recordedHash(id: string, ledger: ModelKnowledgeEntry | null, studies: RankingStudyView[]): string | null {
  if (ledger) return ledger.identity.spec_hash;
  for (const view of studies) {
    const run = latestRankingRun(view.runs);
    if (run?.candidate.candidate_id === id) return run.candidate.spec_hash;
    if (run?.reference?.candidate_id === id) return run.reference.spec_hash;
  }
  return null;
}

/** Every ranking model a study or a knowledge ledger records, once. */
export function buildModelViews(
  ranking: RankingResearch | null,
  knowledge: ModelKnowledge | null,
  live: LiveModels,
): ModelView[] {
  const studies = ranking?.studies ?? [];
  const candidates = new Map((ranking?.candidates ?? []).map((c) => [c.candidate_id, c]));
  for (const view of studies) {
    for (const c of [view.candidate, view.reference]) if (c) candidates.set(c.candidate_id, c);
  }
  const ledgers = new Map(
    (knowledge?.models ?? []).filter((m) => m.kind === "ranking_model").map((m) => [m.id, m]),
  );
  const ids = [...new Set([...candidates.keys(), ...ledgers.keys()])];
  const views = ids.map((id) => {
    const candidate = candidates.get(id) ?? null;
    const ledger = ledgers.get(id) ?? null;
    const direction = candidate?.direction ?? ledger?.identity.direction ?? null;
    const specHash = recordedHash(id, ledger, studies);
    return {
      id,
      name: candidate?.name ?? id,
      direction,
      candidate,
      ledger,
      specHash,
      live: liveState({ id, direction, specHash }, live),
      asCandidate: studies.filter((s) => s.study.candidate === id),
      asReference: studies.filter((s) => s.study.reference === id),
    };
  });
  const order = (v: ModelView) => (v.live === "live" ? 0 : 1);
  return views.sort(
    (a, b) => order(a) - order(b) || (a.direction ?? "").localeCompare(b.direction ?? "") || a.name.localeCompare(b.name),
  );
}

/**
 * Ranking models and Truffle qualification policies in one place: what
 * each model computes, whether Sniffer uses it now, what evidence exists
 * for it, and what remains unproven. Studies sit with the model they
 * evaluate; hashes, runs and ledger revisions are one click away.
 */
export function ResearchModels({
  ranking,
  knowledge,
  live,
}: {
  ranking: Loaded<RankingResearch>;
  knowledge: Loaded<ModelKnowledge>;
  live: LiveModels;
}) {
  const research = ranking.status === "ok" ? ranking.data : null;
  const ledgers = knowledge.status === "ok" ? knowledge.data : null;
  const models = buildModelViews(research, ledgers, live);
  const policies = ledgers?.models.filter((m) => m.kind === "qualification_policy") ?? [];
  return (
    <div className="max-w-5xl space-y-10">
      <ArtifactIssues issues={[...(research?.issues ?? []), ...(ledgers?.issues ?? [])]} />
      <div className="max-w-3xl space-y-2 text-sm leading-relaxed text-muted-foreground">
        <p>
          A ranking model scores every coin of a frame from its features; Sniffer shows the
          configured models&apos; rankings live. Research and Sniffer share the same model files,
          but research never changes a live ranking — and being live says nothing about whether a
          model works.
        </p>
        <p>
          Weights are chosen by hand and nothing has been optimized. A model counts as supported
          only by independent prospective evidence; development results are exploratory.
        </p>
      </div>
      {live.status === "unknown" && (
        <p role="status" className="text-sm text-muted-foreground">
          Live status cannot be verified right now ({live.reason}); no model is marked live.
        </p>
      )}

      <section aria-labelledby="models" className="space-y-4">
        <h2 id="models" className="text-lg font-semibold tracking-tight">
          Ranking models
        </h2>
        {ranking.status !== "ok" && <ResearchUnavailable message={`Ranking studies: ${ranking.message}`} />}
        {knowledge.status !== "ok" && <ResearchUnavailable message={`Model knowledge: ${knowledge.message}`} />}
        {models.length === 0 ? (
          <p className="text-sm text-muted-foreground">No ranking model is recorded yet.</p>
        ) : (
          models.map((m) => <ModelCard key={m.id} model={m} />)
        )}
      </section>

      <section aria-labelledby="truffles" className="space-y-4">
        <div className="space-y-1">
          <h2 id="truffles" className="text-lg font-semibold tracking-tight">
            Truffle qualification
          </h2>
          <p className="max-w-3xl text-sm text-muted-foreground">
            A Truffle rule is applied after ranking: it reads the same frame&apos;s features and
            highlights coins that meet every condition. It never changes a score or an order, and
            its prospective evaluation tests the rule, not the ranking model.
          </p>
        </div>
        {knowledge.status !== "ok" ? null : policies.length === 0 ? (
          <p className="text-sm text-muted-foreground">No qualification policy has a knowledge ledger yet.</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {policies.map((p) => (
              <PolicyCard key={p.id} policy={p} />
            ))}
          </div>
        )}
        {ledgers && (
          <div className="space-y-4">
            <h3 className="font-semibold">Prospective Truffle evaluations</h3>
            {ledgers.truffle_registrations.length === 0 ? (
              <p className="text-sm text-muted-foreground">No Truffle evaluation is registered.</p>
            ) : (
              ledgers.truffle_registrations.map((r) => <Registration key={r.registration_id} registration={r} />)
            )}
          </div>
        )}
      </section>
    </div>
  );
}

const LIVE_TEXT: Record<LiveState, string> = {
  live: "Live in Sniffer — experimental model",
  not_live: "Not used by Sniffer",
  spec_differs: "Sniffer uses a different specification under this name",
  unknown: "Live status unknown",
};

export function LiveBadge({ state }: { state: LiveState }) {
  return (
    <span
      data-live={state}
      className={cn(
        "inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        state === "live"
          ? "border-primary/50 bg-primary/10 text-primary"
          : state === "spec_differs"
            ? "border-destructive/50 text-destructive"
            : "border-border text-muted-foreground",
      )}
    >
      {LIVE_TEXT[state]}
    </span>
  );
}

export function ModelStatusBadge({ status }: { status: string }) {
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex w-fit items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        UNVALIDATED.has(status)
          ? "bg-amber-500/15 text-amber-900 dark:text-amber-200"
          : status === "supported_by_prospective_evidence"
            ? "bg-primary/15 text-primary"
            : "bg-destructive/15 text-destructive",
      )}
    >
      {modelStatusLabel(status)}
    </span>
  );
}

function ModelCard({ model }: { model: ModelView }) {
  const { candidate, ledger } = model;
  return (
    <article
      id={model.id}
      data-model={model.id}
      className="scroll-mt-6 space-y-5 rounded-2xl bg-card p-5 ring-1 ring-foreground/10 md:p-6"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <DirectionTag direction={model.direction} />
            <LiveBadge state={model.live} />
          </div>
          <h3 className="text-lg font-semibold tracking-tight">{model.name}</h3>
          {candidate?.objective && <p className="text-sm text-muted-foreground">{candidate.objective}</p>}
        </div>
        {ledger ? (
          <ModelStatusBadge status={ledger.status} />
        ) : (
          <span className="text-xs text-muted-foreground">Experimental — no knowledge ledger</span>
        )}
      </header>

      <div className="space-y-1 text-sm">
        <p className="font-medium">How it scores</p>
        {candidate ? (
          <>
            <ul className="list-disc space-y-0.5 pl-5">
              {candidate.terms.map((t) => (
                <li key={t.feature}>{describeTerm(t)}</li>
              ))}
            </ul>
            <p className="text-xs text-muted-foreground">
              Summed into one score; a higher score means more attractive for a {candidate.direction}.
              Coins missing any of these features are not ranked.
            </p>
          </>
        ) : (
          <p className="text-muted-foreground">
            Specification file not found; recorded features: {ledger?.identity.features.join(", ") ?? "—"}.
          </p>
        )}
      </div>

      <div className="space-y-3">
        <p className="text-sm font-medium">Evidence</p>
        {model.asCandidate.length === 0 && model.asReference.length === 0 && (
          <p className="text-sm text-muted-foreground">No ranking study has evaluated this model.</p>
        )}
        {model.asCandidate.map((view) => (
          <StudyEvidence key={view.study.study_id} view={view} />
        ))}
        {model.asReference.map((view) => (
          <p key={view.study.study_id} className="text-sm text-muted-foreground">
            Used as the simpler reference in{" "}
            <a href={`#${view.study.study_id}`} className="text-foreground underline-offset-4 hover:underline">
              {view.study.name}
            </a>
            .
          </p>
        ))}
      </div>

      {ledger && ledger.limitations.length > 0 && (
        <div className="space-y-1 text-sm">
          <p className="font-medium">What remains unproven</p>
          <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
            {ledger.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      {ledger && ledger.chain_problems.length > 0 && (
        <p role="alert" className="text-sm text-destructive">
          Knowledge ledger integrity: {ledger.chain_problems.join("; ")}
        </p>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Technical details</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 font-mono sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">id</dt>
          <dd>{model.id}</dd>
          <dt className="text-muted-foreground">spec</dt>
          <dd>{model.specHash ? shortHash(model.specHash) : "not recorded"}</dd>
          {candidate && (
            <>
              <dt className="text-muted-foreground">formula</dt>
              <dd>
                {candidate.terms.map((t) => `${t.weight} × ${t.transform}(${t.feature})`).join(" + ")} ·{" "}
                {candidate.model_family} · missing: {candidate.missing_policy}
              </dd>
            </>
          )}
          {ledger && (
            <>
              <dt className="text-muted-foreground">ledger</dt>
              <dd>
                revision {ledger.revision} of {ledger.revisions} · {formatTimestamp(ledger.derived_at)} ·{" "}
                {shortHash(ledger.content_hash)}
              </dd>
              <dt className="text-muted-foreground">evidence</dt>
              <dd>
                <ul className="space-y-0.5">
                  {ledger.evidence.map((e) => (
                    <li key={`${e.class}-${e.ref}-${e.hypothesis_id ?? ""}`}>
                      {e.class.replaceAll("_", " ")} · {e.ref}
                      {e.verdict && ` · ${e.verdict}`}
                      {e.decision && ` · ${e.decision}`}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </details>
    </article>
  );
}

function StudyEvidence({ view }: { view: RankingStudyView }) {
  const { study, candidate, reference } = view;
  const run = latestRankingRun(view.runs);
  return (
    <section
      id={study.study_id}
      aria-label={study.name}
      className="scroll-mt-6 space-y-4 rounded-xl border border-border p-4 text-sm"
    >
      <div className="space-y-1">
        <p className="font-medium">{study.name}</p>
        <p className="text-muted-foreground">{study.question}</p>
      </div>
      <dl className="grid gap-3 sm:grid-cols-3">
        <Item label="Compared with">
          {reference ? reference.name : study.reference ? `${study.reference} (specification missing)` : "The eligible universe (no reference model)"}
        </Item>
        <Item label="Outcome">{describeLabel(study.label)}</Item>
        <Item label="Measure">
          Top {study.top_k} coins&apos; mean outcome minus the eligible coins&apos; at the same time
          {reference ? ", candidate minus reference" : ""}
        </Item>
      </dl>
      {run === null ? (
        <p className="rounded-lg border border-dashed border-border p-3 text-muted-foreground">
          Registered but never run — no evidence exists.
        </p>
      ) : (
        <StudyResult run={run} hasReference={reference !== null} candidateName={candidate?.name ?? study.candidate} />
      )}
      {view.runs.length > 0 && <RunHistory runs={view.runs} />}
    </section>
  );
}

function StudyResult({ run, hasReference, candidateName }: { run: RankingRun; hasReference: boolean; candidateName: string }) {
  if (run.status !== "completed" || !run.report) {
    return (
      <div className="space-y-1">
        <VerdictBadge verdict={null} failed />
        <p className="text-destructive">No evaluation was produced: {run.error ?? "unknown error"}</p>
      </div>
    );
  }
  const report = run.report;
  const primary = report.primary;
  const evidence = primary.verdict === "positive_evidence" || primary.verdict === "negative_evidence";
  const u = primary.uncertainty;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={primary.verdict} size="lg" />
        <span className="text-xs text-muted-foreground">
          Development data (exploratory) · run {formatTimestamp(run.executed_at)}
        </span>
      </div>
      <p>{VERDICT_META[primary.verdict].meaning}</p>
      {primary.verdict_reasons.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted-foreground">
            {primary.verdict === "insufficient_data" ? "Why no conclusion is possible" : "Criteria not met"}
          </p>
          <ul className="list-disc space-y-0.5 pl-5">
            {primary.verdict_reasons.map((r) => (
              <li key={r}>{describeBlocker(parseBlocker(r))}</li>
            ))}
          </ul>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Item label="Days">{formatCount(report.coverage.distinct_days)}</Item>
        <Item label="Timestamps">
          {formatCount(hasReference ? (primary.matched_timestamps ?? null) : report.candidate.timestamps)}
        </Item>
        <Item label="Eligible coins (mean)">
          {report.coverage.mean_universe === null ? "—" : Math.round(report.coverage.mean_universe)}
        </Item>
        <Item label="Horizon">{report.horizon_minutes} min</Item>
      </dl>
      <div className={evidence ? "" : "rounded-lg border border-dashed border-border p-3"}>
        {!evidence && (
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Observed values — descriptive only, not evidence
          </p>
        )}
        <dl className="grid gap-3 sm:grid-cols-3">
          {report.reference && (
            <Item label="Reference: top-K vs market">{formatSpread(report.reference.mean_top_k_excess_utility)}</Item>
          )}
          <Item label={`${hasReference ? "Candidate" : candidateName}: top-K vs market`}>
            {formatSpread(report.candidate.mean_top_k_excess_utility)}
          </Item>
          {hasReference && <Item label="Difference (candidate − reference)">{formatSpread(primary.mean)}</Item>}
        </dl>
      </div>
      <p>
        <span className="text-muted-foreground">Uncertainty: </span>
        {!u.suppressed && u.interval ? (
          <>
            {Math.round((u.confidence_level ?? 0) * 100)}% interval [{formatSpread(u.interval[0])},{" "}
            {formatSpread(u.interval[1])}] <span className="text-muted-foreground">({u.method})</span>
          </>
        ) : (
          <span className="text-muted-foreground">no interval — {u.reason}.</span>
        )}
      </p>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          Standing limitations ({report.disclosures.length})
        </summary>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
          {report.disclosures.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      </details>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Exploratory diagnostics (never decide a result)</summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          {report.reference && <Diagnostics label="Reference" summary={report.reference} />}
          <Diagnostics label="Candidate" summary={report.candidate} />
        </div>
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
      <dd>Mean Spearman (score vs utility): {d.mean_spearman === null ? "—" : d.mean_spearman.toFixed(3)}</dd>
      <dd>
        {summary.distinct_top_symbols} distinct coins in top K · {summary.top_k_missing_outcome} top-K picks
        without an outcome (excluded)
      </dd>
    </dl>
  );
}

function RunHistory({ runs }: { runs: RankingRun[] }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">Runs and provenance ({runs.length})</summary>
      <ul className="mt-2 space-y-2 font-mono">
        {runs.map((run) => (
          <li key={run.run_id} className="space-y-0.5 rounded-lg border border-border p-2">
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
              {formatTimestamp(run.executed_at)} · ranking evaluator v{run.ranking_evaluator_version} · study{" "}
              {shortHash(run.spec_hash)} · candidate {run.candidate.candidate_id} {shortHash(run.candidate.spec_hash)}
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

/** A qualification condition as recorded in the policy ledger, in words. */
function policyCondition(raw: Record<string, unknown>): string {
  const feature = String(raw.feature ?? "");
  const atMost = typeof raw.at_most === "number" ? raw.at_most : null;
  const atLeast = typeof raw.at_least === "number" ? raw.at_least : null;
  const bound = atMost ?? atLeast;
  if (bound === null) return JSON.stringify(raw);
  const condition: Condition = {
    feature,
    transform: raw.measure === "percentile" ? "cs_percentile" : "raw",
    op: atMost !== null ? "<=" : ">=",
    value: bound,
  };
  return describeCondition(condition);
}

function PolicyCard({ policy }: { policy: ModelKnowledgeEntry }) {
  const conditions = Array.isArray(policy.identity.parameters?.conditions)
    ? (policy.identity.parameters.conditions as Record<string, unknown>[])
    : [];
  return (
    <article id={policy.id} className="scroll-mt-6 space-y-3 rounded-xl border border-border p-4 text-sm" data-model={policy.id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <DirectionTag direction={policy.identity.direction} />
        <ModelStatusBadge status={policy.status} />
      </div>
      <div>
        <p className="font-medium">A coin is a {policy.identity.direction === "short" ? "Short" : "Long"} Truffle when:</p>
        {conditions.length ? (
          <ul className="list-disc space-y-0.5 pl-5">
            {conditions.map((c, i) => (
              <li key={i}>{policyCondition(c)}</li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">Conditions not recorded in the ledger.</p>
        )}
        {policy.identity.applies_to && (
          <p className="mt-1 text-xs text-muted-foreground">
            Applied to the ranking of{" "}
            <a href={`#${policy.identity.applies_to.ranking_model}`} className="underline-offset-4 hover:underline">
              {policy.identity.applies_to.ranking_model}
            </a>
            .
          </p>
        )}
      </div>
      {policy.limitations.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {policy.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      )}
      {policy.chain_problems.length > 0 && (
        <p role="alert" className="text-xs text-destructive">
          Knowledge ledger integrity: {policy.chain_problems.join("; ")}
        </p>
      )}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Technical details</summary>
        <p className="mt-1 font-mono text-muted-foreground">
          {policy.id} · spec {shortHash(policy.identity.spec_hash)} · ledger revision {policy.revision} of{" "}
          {policy.revisions}
        </p>
      </details>
    </article>
  );
}

function Registration({ registration }: { registration: TruffleRegistrationView }) {
  const plan = registration.plan;
  return (
    <article
      id={registration.registration_id}
      data-registration={registration.registration_id}
      className="scroll-mt-6 space-y-3 rounded-xl border border-border p-4 text-sm"
    >
      <p className="font-medium">{registration.question}</p>
      <ul className="space-y-1">
        {registration.claims.map((c) => (
          <li key={c.hypothesis_id} className="flex flex-wrap items-start gap-2">
            <DirectionTag direction={c.direction} />
            <span>{c.statement}</span>
          </li>
        ))}
      </ul>
      <table className="w-full text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Window</th>
            <th className="py-1 pr-3 font-medium">Days with data</th>
            <th className="py-1 pr-3 font-medium">State</th>
            <th className="py-1 font-medium">Decisions</th>
          </tr>
        </thead>
        <tbody>
          {registration.partitions.map((p) => (
            <tr key={p.role} data-lifecycle={p.lifecycle}>
              <td className="py-1 pr-3">
                {p.role} {p.start} → {p.end} (excl.)
              </td>
              <td className="py-1 pr-3">
                {p.days_with_shard}/{p.days}
              </td>
              <td className="py-1 pr-3">
                {lifecycleLabel(p.lifecycle)}
                {p.lifecycle === "registered_awaiting_data" && ` · eligible from ${formatTimestamp(p.eligible_from)}`}
              </td>
              <td className="py-1">
                {Object.keys(p.decisions).length === 0
                  ? "—"
                  : Object.entries(p.decisions)
                      .map(([h, d]) => `${h}: ${d}`)
                      .join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground">
        Compared with: {plan.primary_reference}. Multiplicity: {plan.multiplicity.method} over{" "}
        {plan.multiplicity.family_size} primary claims (adjusted interval{" "}
        {Math.round(plan.multiplicity.adjusted_confidence * 100)}%).
      </p>
      {registration.limitations.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">
            Limitations ({registration.limitations.length})
          </summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
            {registration.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </details>
      )}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">Technical details</summary>
        <ul className="mt-1 space-y-0.5 font-mono text-muted-foreground">
          <li>
            {registration.registration_id} · registered {formatTimestamp(registration.registered_at)} ·{" "}
            {shortHash(registration.registration_hash)}
          </li>
          {registration.claims.map((c) => (
            <li key={c.hypothesis_id}>
              {c.hypothesis_id} · {c.qualification_id} ({shortHash(c.qualification_spec_hash)}) on{" "}
              {c.ranking_candidate} ({shortHash(c.ranking_spec_hash)}) · primary {c.primary_label}, secondary{" "}
              {c.secondary_labels.join(", ")}
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}
