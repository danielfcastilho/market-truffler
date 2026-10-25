import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag } from "@/components/research/knowledge-badge";
import {
  formatTimestamp,
  lifecycleLabel,
  modelStatusLabel,
  shortHash,
  type ModelKnowledge,
  type ModelKnowledgeEntry,
  type TruffleRegistrationView,
} from "@/lib/research";
import { cn } from "@/lib/utils";

const UNVALIDATED = new Set([
  "experimental",
  "registered_for_prospective_evaluation",
  "insufficient_prospective_evidence",
  "inconclusive_prospective_evidence",
]);

/**
 * What is known about each ranking model and Truffle qualification policy,
 * and the prospective registrations that bind them. Everything shown is a
 * recorded ledger revision or registration; nothing is evaluated here.
 */
export function ModelKnowledgeView({ knowledge }: { knowledge: ModelKnowledge }) {
  const models = knowledge.models.filter((m) => m.kind === "ranking_model");
  const policies = knowledge.models.filter((m) => m.kind === "qualification_policy");
  return (
    <div className="max-w-5xl space-y-10">
      <ArtifactIssues issues={knowledge.issues} />
      <div role="note" className="rounded-xl border border-amber-500/50 bg-amber-500/10 p-4 text-sm">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Registering a model is not validating it.
        </p>
        <p className="mt-1 text-muted-foreground">
          A status only moves on independent prospective evidence, verified against its exposure
          log. Live use, Truffle counts, development results and the number of experiments never
          change it.
        </p>
      </div>

      <section className="space-y-4" aria-label="Prospective Truffle evaluations">
        <h2 className="text-lg font-semibold">Prospective Truffle evaluations</h2>
        {knowledge.truffle_registrations.length === 0 ? (
          <p className="text-sm text-muted-foreground">No Truffle evaluation is registered.</p>
        ) : (
          knowledge.truffle_registrations.map((r) => <Registration key={r.registration_id} registration={r} />)
        )}
      </section>

      <section className="space-y-4" aria-label="Qualification policies">
        <h2 className="text-lg font-semibold">Qualification policies</h2>
        <Entries entries={policies} empty="No qualification policy has a knowledge ledger yet." />
      </section>

      <section className="space-y-4" aria-label="Ranking models">
        <h2 className="text-lg font-semibold">Ranking models</h2>
        <Entries entries={models} empty="No ranking model has a knowledge ledger yet." />
      </section>
    </div>
  );
}

function Entries({ entries, empty }: { entries: ModelKnowledgeEntry[]; empty: string }) {
  if (entries.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {entries.map((m) => (
        <Entry key={m.id} entry={m} />
      ))}
    </div>
  );
}

export function ModelStatusBadge({ status }: { status: string }) {
  return (
    <span
      data-status={status}
      className={cn(
        "rounded-md px-2 py-0.5 font-mono text-xs",
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

function Entry({ entry }: { entry: ModelKnowledgeEntry }) {
  const identity = entry.identity;
  const evidence = entry.evidence.filter((e) => e.class !== "human_hypothesis");
  return (
    <article className="space-y-3 rounded-xl border border-border p-4 text-sm" data-model={entry.id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono font-medium">{entry.id}</p>
        <ModelStatusBadge status={entry.status} />
      </div>
      <p className="flex flex-wrap items-center gap-2 font-mono text-xs text-muted-foreground">
        <DirectionTag direction={identity.direction} />
        <span>{entry.kind === "ranking_model" ? identity.model_family : "qualification policy"}</span>
        <span>· spec {shortHash(identity.spec_hash)}</span>
        <span>· features {identity.features.join(", ")}</span>
        {identity.applies_to && <span>· applies to {identity.applies_to.ranking_model}</span>}
      </p>
      <div>
        <p className="font-mono text-xs font-medium text-muted-foreground">Evidence</p>
        {evidence.length === 0 ? (
          <p className="text-xs text-muted-foreground">None recorded.</p>
        ) : (
          <ul className="space-y-0.5 font-mono text-xs">
            {evidence.map((e) => (
              <li key={`${e.class}-${e.ref}-${e.hypothesis_id ?? ""}`}>
                <span className="text-muted-foreground">{e.class.replaceAll("_", " ")}</span> · {e.ref}
                {e.verdict && ` · ${e.verdict}`}
                {e.decision && ` · ${e.decision}`}
                {e.via_qualification_policy && ` (via ${e.via_qualification_policy})`}
              </li>
            ))}
          </ul>
        )}
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Limitations ({entry.limitations.length})
        </summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {entry.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>
      <p className="font-mono text-[11px] text-muted-foreground">
        revision {entry.revision} of {entry.revisions} · {formatTimestamp(entry.derived_at)} ·{" "}
        {shortHash(entry.content_hash)}
      </p>
      {entry.chain_problems.length > 0 && (
        <p role="alert" className="font-mono text-xs text-destructive">
          Ledger integrity: {entry.chain_problems.join("; ")}
        </p>
      )}
    </article>
  );
}

function Registration({ registration }: { registration: TruffleRegistrationView }) {
  const plan = registration.plan;
  return (
    <article className="space-y-3 rounded-xl border border-border p-4 text-sm" data-registration={registration.registration_id}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-mono font-medium">{registration.registration_id}</p>
        <p className="font-mono text-xs text-muted-foreground">
          registered {formatTimestamp(registration.registered_at)} · {shortHash(registration.registration_hash)}
        </p>
      </div>
      <p className="text-muted-foreground">{registration.question}</p>
      <table className="w-full text-left font-mono text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Partition</th>
            <th className="py-1 pr-3 font-medium">Days</th>
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
      <ul className="space-y-1 text-xs">
        {registration.claims.map((c) => (
          <li key={c.hypothesis_id} className="font-mono">
            <span className="font-semibold">{c.hypothesis_id}</span> · {c.qualification_id} (
            {shortHash(c.qualification_spec_hash)}) on {c.ranking_candidate} ({shortHash(c.ranking_spec_hash)}) ·
            primary {c.primary_label}, secondary {c.secondary_labels.join(", ")}
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        Reference: {plan.primary_reference}. Multiplicity: {plan.multiplicity.method} over{" "}
        {plan.multiplicity.family_size} primary claims (adjusted interval{" "}
        {Math.round(plan.multiplicity.adjusted_confidence * 100)}%).
      </p>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Limitations ({registration.limitations.length})
        </summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {registration.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}
