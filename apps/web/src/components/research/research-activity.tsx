import Link from "next/link";

import {
  KNOWLEDGE_META,
  VERDICT_META,
  formatTimestamp,
  isKnowledgeStatus,
  isVerdict,
  type ResearchEvent,
} from "@/lib/research";

/**
 * Research activity as recorded by artifacts — campaign registrations,
 * evaluations (runs grouped by campaign and UTC day; repeats of an
 * already-run experiment are counted, never presented as new evidence) and
 * knowledge ledger revisions with the status changes they recorded.
 */
export function ResearchActivity({ events }: { events: ResearchEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-muted-foreground">No research activity recorded yet.</p>;
  }
  return (
    <ol className="relative space-y-5 border-l border-border pl-5">
      {events.map((event, n) => (
        <li key={n} className="relative">
          <span className="absolute top-1.5 -left-[1.4rem] size-2 rounded-full bg-border ring-4 ring-background" />
          <p className="font-mono text-xs text-muted-foreground">
            {event.at.length === 10 ? event.at : formatTimestamp(event.at)}
          </p>
          <EventBody event={event} />
        </li>
      ))}
    </ol>
  );
}

function CampaignLink({ id }: { id: string }) {
  return (
    <Link href={`/oink-corp/campaigns/${id}`} className="font-medium underline-offset-4 hover:underline">
      {id}
    </Link>
  );
}

function EventBody({ event }: { event: ResearchEvent }) {
  if (event.type === "campaign_registered") {
    return (
      <div className="text-sm">
        <p>
          Campaign registered: <CampaignLink id={event.campaign_id} />
        </p>
        <p className="text-muted-foreground">{event.title}</p>
      </div>
    );
  }
  if (event.type === "evaluation_registered") {
    return (
      <div className="text-sm">
        <p>
          Prospective evaluation registered for <CampaignLink id={event.campaign_id} />
        </p>
        <p className="text-muted-foreground">
          {event.registration_id}: {event.hypotheses.join(", ")} frozen;{" "}
          {event.partitions.map((p) => `${p.role} ${p.start} → ${p.end}`).join(", ")} reserved
          (no outcomes from these periods existed at registration).
        </p>
      </div>
    );
  }
  if (event.type === "ranking_evaluation") {
    return (
      <div className="text-sm">
        <p>
          Ranking research evaluated:{" "}
          <Link href="/oink-corp/ranking" className="font-medium underline-offset-4 hover:underline">
            {event.experiment_ids.join(", ")}
          </Link>
        </p>
        <p className="text-muted-foreground">
          {event.runs} run{event.runs === 1 ? "" : "s"}
          {event.repeat_runs > 0 && ` (${event.repeat_runs} repeat${event.repeat_runs === 1 ? "" : "s"} — not new evidence)`}{" "}
          · development only · {Object.entries(event.verdicts).filter(([v]) => isVerdict(v)).map(([v, n]) => `${VERDICT_META[v as keyof typeof VERDICT_META].label} × ${n}`).join(", ") || "no verdicts"}
        </p>
      </div>
    );
  }
  if (event.type === "knowledge_revision") {
    return (
      <div className="text-sm">
        <p>
          Knowledge ledger revision {event.revision} for <CampaignLink id={event.campaign_id} />
        </p>
        {event.changes.length === 0 ? (
          <p className="text-muted-foreground">No status changed.</p>
        ) : (
          <ul className="text-muted-foreground">
            {event.changes.map((change) => (
              <li key={change.hypothesis_id}>
                {change.hypothesis_id}:{" "}
                {change.from && isKnowledgeStatus(change.from)
                  ? KNOWLEDGE_META[change.from].label
                  : "first recorded"}{" "}
                → {isKnowledgeStatus(change.to) ? KNOWLEDGE_META[change.to].label : change.to}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  const verdicts = Object.entries(event.verdicts)
    .filter(([v]) => isVerdict(v))
    .map(([v, n]) => `${VERDICT_META[v as keyof typeof VERDICT_META].label} × ${n}`);
  return (
    <div className="text-sm">
      <p>
        {event.campaign_id ? (
          <>
            Evaluated: <CampaignLink id={event.campaign_id} />
          </>
        ) : (
          <>
            Evaluated:{" "}
            <Link
              href={`/oink-corp/experiments/${event.experiment_ids[0]}`}
              className="font-medium underline-offset-4 hover:underline"
            >
              {event.experiment_ids[0]}
            </Link>
          </>
        )}
      </p>
      <p className="text-muted-foreground">
        {event.runs} run{event.runs === 1 ? "" : "s"}
        {event.repeat_runs > 0 && ` (${event.repeat_runs} repeat${event.repeat_runs === 1 ? "" : "s"} of earlier runs — not new evidence)`}
        {event.failed_runs > 0 && `, ${event.failed_runs} failed`} · {event.segments.join(", ")} ·{" "}
        {verdicts.join(", ") || "no verdicts"}
      </p>
    </div>
  );
}
