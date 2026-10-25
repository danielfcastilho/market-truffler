import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag, KnowledgeBadge } from "@/components/research/knowledge-badge";
import { ResearchActivity } from "@/components/research/research-activity";
import { VerdictBadge } from "@/components/research/verdict-badge";
import { lifecycleLabel, type ArtifactIssue, type ResearchEvent } from "@/lib/research";
import {
  describeBlocker,
  type QuestionStatus,
  type ResearchQuestion,
  type ScientificSummary,
} from "@/lib/research-questions";
import { cn } from "@/lib/utils";

const RECENT_EVENTS = 5;

const GROUPS: { kind: ResearchQuestion["kind"]; title: string; note: string }[] = [
  {
    kind: "ranking_study",
    title: "Ranking models",
    note: "Do a model's top picks beat the market, or a simpler model?",
  },
  {
    kind: "hypothesis",
    title: "Market hypotheses",
    note: "Does a market condition tell us something about what happens next?",
  },
  {
    kind: "prospective_registration",
    title: "Prospective evaluations",
    note: "Claims frozen in advance and tested only on data collected afterwards.",
  },
];

/**
 * OINK CORP's landing page: what is being investigated, what the recorded
 * evidence supports, and what stands in the way — derived from research
 * records (`lib/research-questions`), with every record one click away.
 */
export function ResearchOverview({
  questions,
  summary,
  activity,
  issues,
}: {
  questions: ResearchQuestion[];
  summary: ScientificSummary;
  activity: ResearchEvent[] | null;
  issues: ArtifactIssue[];
}) {
  return (
    <div className="max-w-5xl space-y-10">
      <ArtifactIssues issues={issues} />

      <section aria-labelledby="state" className="space-y-3">
        <h2 id="state" className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
          State of research
        </h2>
        <p className="max-w-3xl text-xl leading-snug font-semibold tracking-tight">{summary.headline}</p>
        {summary.breakdown.length > 0 && (
          <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
            {summary.breakdown.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
        {summary.blockers.length > 0 && (
          <div className="max-w-3xl rounded-lg border border-dashed border-border p-3 text-sm">
            <p className="font-medium">What prevents stronger conclusions</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
              {summary.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        )}
        {summary.missing.length > 0 && (
          <p role="status" className="text-sm text-destructive">
            Not included — could not be loaded: {summary.missing.join(", ")}. Nothing is concluded
            about them.
          </p>
        )}
        <p className="max-w-3xl text-xs text-muted-foreground">
          Exploratory evidence comes from development data only. A finding counts as validated only
          when independent data — prospective or never used before — supports it. None of this is
          a trading signal.
        </p>
      </section>

      {GROUPS.map((group) => {
        const items = questions.filter((q) => q.kind === group.kind);
        if (items.length === 0) return null;
        return (
          <section key={group.kind} aria-labelledby={`group-${group.kind}`} className="space-y-3">
            <div>
              <h2 id={`group-${group.kind}`} className="text-lg font-semibold tracking-tight">
                {group.title}
              </h2>
              <p className="text-sm text-muted-foreground">{group.note}</p>
            </div>
            <ul className="divide-y divide-border rounded-xl ring-1 ring-foreground/10">
              {items.map((q) => (
                <QuestionRow key={q.key} question={q} />
              ))}
            </ul>
          </section>
        );
      })}

      {questions.length === 0 && summary.missing.length === 0 && (
        <p className="max-w-xl text-sm text-muted-foreground">
          Questions appear here once a campaign, ranking study or prospective evaluation is
          registered under <code className="font-mono">research/oink_corp/</code>.
        </p>
      )}

      <section aria-labelledby="activity" className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="activity" className="text-lg font-semibold tracking-tight">
            Recent research activity
          </h2>
          <Link href="/oink-corp/history" className="text-sm font-medium text-primary underline-offset-4 hover:underline">
            Audit log: all events and immutable runs →
          </Link>
        </div>
        {activity === null ? (
          <p className="text-sm text-muted-foreground">Research activity could not be loaded.</p>
        ) : (
          <ResearchActivity events={activity.slice(0, RECENT_EVENTS)} />
        )}
      </section>
    </div>
  );
}

function QuestionRow({ question }: { question: ResearchQuestion }) {
  const blocker = question.blockers[0];
  return (
    <li data-question={question.key} className="space-y-2 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            {question.direction === "both" ? (
              <span className="text-xs font-semibold text-muted-foreground uppercase">Long &amp; Short</span>
            ) : (
              <DirectionTag direction={question.direction} />
            )}
            <Link
              href={question.href}
              className="font-medium underline-offset-4 hover:underline focus-visible:underline"
            >
              {question.title}
            </Link>
          </div>
          {question.context && question.context !== question.title && (
            <p className="text-sm text-muted-foreground">{question.context}</p>
          )}
        </div>
        <QuestionStatusBadge status={question.status} />
      </div>
      {blocker && (
        <p className="text-sm">
          <span className="text-muted-foreground">Blocked by: </span>
          {describeBlocker(blocker)}
          {question.blockers.length > 1 && (
            <span className="text-muted-foreground"> · {question.blockers.length - 1} more on the detail page</span>
          )}
        </p>
      )}
      {question.independentTest && <p className="text-sm text-muted-foreground">{question.independentTest}</p>}
      <p className="text-xs text-muted-foreground">{question.source}</p>
    </li>
  );
}

/** The record's own status in its own vocabulary: knowledge status for a
 * hypothesis, run verdict for a ranking study, lifecycle for a registration. */
export function QuestionStatusBadge({ status }: { status: QuestionStatus }) {
  if (status.type === "knowledge") return <KnowledgeBadge status={status.status} />;
  if (status.type === "verdict") {
    return (
      <span className="flex flex-col items-end gap-0.5">
        <VerdictBadge verdict={status.verdict} failed={status.failed} />
        {status.verdict !== null && !status.failed && (
          <span className="text-[11px] text-muted-foreground">development data (exploratory)</span>
        )}
      </span>
    );
  }
  const decided = Object.entries(status.decisions);
  return (
    <span className="flex flex-col items-end gap-0.5">
      <span
        className={cn(
          "inline-flex w-fit items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
          decided.length ? "border-border bg-secondary" : "border-dashed border-muted-foreground/60 bg-muted text-muted-foreground",
        )}
      >
        {lifecycleLabel(status.lifecycle)}
      </span>
      {decided.map(([h, d]) => (
        <span key={h} className="text-[11px] text-muted-foreground">
          {h}: {d.replaceAll("_", " ")}
        </span>
      ))}
    </span>
  );
}
