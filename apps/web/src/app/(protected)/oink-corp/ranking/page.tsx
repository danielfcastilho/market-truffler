import { RankingResearch } from "@/components/research/ranking-research";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getRankingResearch } from "@/lib/server-api";

export default async function RankingResearchPage() {
  const result = await getRankingResearch();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <RankingResearch research={result.data} />;
}
