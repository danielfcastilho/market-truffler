import { ResearchOverview } from "@/components/research/research-overview";
import { deriveQuestions, scientificSummary } from "@/lib/research-questions";
import {
  getModelKnowledge,
  getRankingResearch,
  getResearchCampaign,
  getResearchOverview,
} from "@/lib/server-api";

export default async function OinkCorpPage() {
  const [overview, ranking, models] = await Promise.all([
    getResearchOverview(),
    getRankingResearch(),
    getModelKnowledge(),
  ]);
  const campaigns = overview.status === "ok" ? overview.data.campaigns : [];
  // Campaign details carry each hypothesis' recorded limitations (its blockers).
  const details = await Promise.all(campaigns.map((c) => getResearchCampaign(c.campaign_id)));

  const missing = [
    overview.status !== "ok" && "market hypotheses",
    ranking.status !== "ok" && "ranking studies",
    models.status !== "ok" && "prospective evaluations",
  ].filter((s): s is string => Boolean(s));
  const questions = deriveQuestions({
    overview: overview.status === "ok" ? overview.data : null,
    campaignDetails: Object.fromEntries(
      campaigns.map((c, i) => [c.campaign_id, details[i].status === "ok" ? details[i].data : null]),
    ),
    ranking: ranking.status === "ok" ? ranking.data : null,
    models: models.status === "ok" ? models.data : null,
  });
  const issues = [
    ...(overview.status === "ok" ? overview.data.issues : []),
    ...(ranking.status === "ok" ? ranking.data.issues : []),
    ...(models.status === "ok" ? models.data.issues : []),
  ];

  return (
    <ResearchOverview
      questions={questions}
      summary={scientificSummary(questions, missing)}
      activity={overview.status === "ok" ? overview.data.activity : null}
      issues={issues}
    />
  );
}
