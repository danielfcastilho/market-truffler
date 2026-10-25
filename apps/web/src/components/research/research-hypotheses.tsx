import Link from "next/link";

import { ArtifactIssues } from "@/components/research/artifact-issues";
import { DirectionTag, KnowledgeBadge } from "@/components/research/knowledge-badge";
import { independentTestFor } from "@/lib/research-questions";
import { hypothesisTitle, type ResearchOverview as Overview } from "@/lib/research";

/**
 * Every pre-registered hypothesis, by campaign: its readable title and its
 * knowledge status, with the campaign page (claim, prediction, evidence,
 * limits, provenance) one click away.
 */
export function ResearchHypotheses({ overview }: { overview: Overview }) {
  return (
    <div className="max-w-5xl space-y-8">
      <ArtifactIssues issues={overview.issues} />
      {overview.campaigns.length === 0 ? (
        <div className="max-w-xl space-y-2">
          <p className="font-medium">No hypothesis has been registered yet.</p>
          <p className="text-sm text-muted-foreground">
            Hypotheses appear here once a campaign is pre-registered in{" "}
            <code className="font-mono">research/oink_corp/campaigns/</code>.
          </p>
        </div>
      ) : (
        overview.campaigns.map((campaign) => <CampaignCard key={campaign.campaign_id} campaign={campaign} />)
      )}
    </div>
  );
}

function CampaignCard({ campaign }: { campaign: Overview["campaigns"][number] }) {
  const groups = (["long", "short"] as const)
    .map((direction) => ({
      direction,
      hypotheses: campaign.hypotheses.filter((h) => h.direction === direction),
    }))
    .filter((g) => g.hypotheses.length > 0);
  const undirected = campaign.hypotheses.filter((h) => h.direction !== "long" && h.direction !== "short");
  const plan = independentTestFor(campaign.evaluations, null);
  return (
    <article className="space-y-5 rounded-2xl bg-card p-5 ring-1 ring-foreground/10 md:p-6">
      <header className="space-y-1">
        <h2 className="text-lg leading-snug font-semibold tracking-tight">{campaign.question}</h2>
        <p className="text-xs text-muted-foreground">Registered {campaign.created ?? "—"}</p>
      </header>
      <div className="grid gap-5 md:grid-cols-2">
        {groups.map(({ direction, hypotheses }) => (
          <div key={direction} className="space-y-2">
            <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
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
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">Independent test: </span>
        {plan ?? "none registered — all evidence is historical."}
      </p>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4 text-sm">
        <span className="text-muted-foreground">
          {campaign.ledger_revision === null
            ? "Knowledge not yet derived for this campaign."
            : "Statuses from the campaign's knowledge ledger."}
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
        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <span className="flex items-center gap-2">
          <DirectionTag direction={hypothesis.direction} />
          <span className="text-sm font-medium">{hypothesisTitle(hypothesis.conditions)}</span>
          <span className="text-xs text-muted-foreground">{hypothesis.hypothesis_id}</span>
        </span>
        <KnowledgeBadge status={hypothesis.knowledge_status} />
      </Link>
    </li>
  );
}
