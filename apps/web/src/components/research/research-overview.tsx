import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag, KnowledgeBadge } from "@/components/research/knowledge-badge";
import { ResearchActivity } from "@/components/research/research-activity";
import {
  KNOWLEDGE_META,
  hypothesisTitle,
  isKnowledgeStatus,
  type KnowledgeStatus,
  type ResearchOverview as Overview,
} from "@/lib/research";

/** Statuses that exist only after an independent validation run. */
const INDEPENDENTLY_VALIDATED: KnowledgeStatus[] = [
  "independently_supported",
  "independently_contradicted",
  "independently_observed_difference",
  "conflicting_evidence",
];
const PRELIMINARY: KnowledgeStatus[] = [
  "preliminary_support",
  "preliminary_contradiction",
  "preliminary_difference",
];

/**
 * What OINK CORP is investigating and what it currently knows: hypotheses
 * and their knowledge status (from each campaign's knowledge ledger) first,
 * research activity second, execution detail one click away.
 */
export function ResearchOverview({ overview }: { overview: Overview }) {
  const { counts, knowledge, campaigns } = overview;
  if (counts.campaigns === 0 && counts.runs === 0 && counts.experiments_registered === 0) {
    return (
      <div className="max-w-xl space-y-2">
        <p className="font-medium">No research has been registered yet.</p>
        <p className="text-sm text-muted-foreground">
          Hypotheses appear here once a campaign is pre-registered in{" "}
          <code className="font-mono">research/oink_corp/campaigns/</code>, and knowledge once its
          experiments have been evaluated. Nothing is shown until then.
        </p>
      </div>
    );
  }
  const sum = (statuses: KnowledgeStatus[]) =>
    statuses.reduce((total, s) => total + (knowledge.by_status[s] ?? 0), 0);
  const statusLine = Object.entries(knowledge.by_status)
    .filter(([status, n]) => isKnowledgeStatus(status) && n)
    .map(([status, n]) => `${n} ${KNOWLEDGE_META[status as KnowledgeStatus].label.toLowerCase()}`);

  return (
    <div className="max-w-5xl space-y-12">
      <ArtifactIssues issues={overview.issues} />

      <section aria-labelledby="intelligence" className="space-y-3">
        <SectionTitle id="intelligence">Research intelligence</SectionTitle>
        <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Research campaigns" value={counts.campaigns} />
          <Stat label="Registered hypotheses" value={counts.hypotheses} />
          <Stat label="With preliminary evidence" value={sum(PRELIMINARY)} />
          <Stat label="Independently validated" value={sum(INDEPENDENTLY_VALIDATED)} />
        </dl>
        <p className="text-sm text-muted-foreground">
          Current knowledge: {statusLine.join(", ") || "none derived yet"}
          {knowledge.hypotheses_without_ledger > 0 &&
            ` · ${knowledge.hypotheses_without_ledger} hypotheses not yet in a knowledge ledger`}
          . No hypothesis counts as an established finding until independent validation supports
          it.
        </p>
      </section>

      <section aria-labelledby="investigations" className="space-y-4">
        <SectionTitle id="investigations">Active investigations</SectionTitle>
        {campaigns.length === 0 ? (
          <p className="text-sm text-muted-foreground">No campaigns registered.</p>
        ) : (
          campaigns.map((campaign) => <InvestigationCard key={campaign.campaign_id} campaign={campaign} />)
        )}
      </section>

      <section aria-labelledby="activity" className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <SectionTitle id="activity">Recent research activity</SectionTitle>
          <Link href="/oink-corp/history" className="text-sm font-medium text-primary underline-offset-4 hover:underline">
            View research history →
          </Link>
        </div>
        <ResearchActivity events={overview.activity} />
        <p className="font-mono text-xs text-muted-foreground">
          {counts.runs} immutable runs recorded{counts.runs_failed ? ` (${counts.runs_failed} failed)` : ""}
          {" · "}
          {counts.experiments_executed}/{counts.experiments_registered} experiments and{" "}
          {counts.comparisons_executed}/{counts.comparisons_registered} comparisons run
        </p>
      </section>
    </div>
  );
}

function InvestigationCard({ campaign }: { campaign: Overview["campaigns"][number] }) {
  const groups = (["long", "short"] as const)
    .map((direction) => ({
      direction,
      hypotheses: campaign.hypotheses.filter((h) => h.direction === direction),
    }))
    .filter((g) => g.hypotheses.length > 0);
  const undirected = campaign.hypotheses.filter((h) => h.direction !== "long" && h.direction !== "short");
  return (
    <article className="space-y-5 rounded-2xl bg-card p-5 ring-1 ring-foreground/10 md:p-6">
      <header className="space-y-1.5">
        <p className="font-mono text-xs text-muted-foreground">
          {campaign.campaign_id} · registered {campaign.created ?? "—"}
        </p>
        <h3 className="text-lg leading-snug font-semibold tracking-tight">{campaign.question}</h3>
      </header>
      <div className="grid gap-5 md:grid-cols-2">
        {groups.map(({ direction, hypotheses }) => (
          <div key={direction} className="space-y-2">
            <p className="font-mono text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {direction} hypotheses
            </p>
            <ul className="space-y-2">
              {hypotheses.map((h) => (
                <HypothesisRow key={h.hypothesis_id} campaignId={campaign.campaign_id} hypothesis={h} />
              ))}
            </ul>
          </div>
        ))}
        {undirected.length > 0 && (
          <ul className="space-y-2">
            {undirected.map((h) => (
              <HypothesisRow key={h.hypothesis_id} campaignId={campaign.campaign_id} hypothesis={h} />
            ))}
          </ul>
        )}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4 text-sm">
        <span className="text-muted-foreground">
          {campaign.ledger_revision === null
            ? "Knowledge not yet derived for this campaign."
            : `Knowledge ledger revision ${campaign.ledger_revision}`}
          {campaign.ledger_problems > 0 && (
            <span className="ml-2 font-medium text-destructive">
              {campaign.ledger_problems} cited run{campaign.ledger_problems === 1 ? "" : "s"} do not
              match their records
            </span>
          )}
        </span>
        <Link
          href={`/oink-corp/campaigns/${campaign.campaign_id}`}
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          Explore campaign →
        </Link>
      </footer>
    </article>
  );
}

function HypothesisRow({
  campaignId,
  hypothesis,
}: {
  campaignId: string;
  hypothesis: Overview["campaigns"][number]["hypotheses"][number];
}) {
  return (
    <li>
      <Link
        href={`/oink-corp/campaigns/${campaignId}#${hypothesis.hypothesis_id}`}
        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 transition-colors hover:bg-muted/50"
      >
        <span className="flex items-center gap-2">
          <DirectionTag direction={hypothesis.direction} />
          <span className="text-sm font-medium">{hypothesisTitle(hypothesis.conditions)}</span>
          <span className="font-mono text-xs text-muted-foreground">{hypothesis.hypothesis_id}</span>
        </span>
        <KnowledgeBadge status={hypothesis.knowledge_status} />
      </Link>
    </li>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="font-mono text-xs font-semibold tracking-widest text-muted-foreground uppercase">
      {children}
    </h2>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-card p-4 ring-1 ring-foreground/10">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</dd>
    </div>
  );
}
