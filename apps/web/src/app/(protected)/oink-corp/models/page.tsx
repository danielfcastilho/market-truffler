import { ResearchModels } from "@/components/research/research-models";
import { liveModels } from "@/lib/research-questions";
import { getLiveRankings, getModelKnowledge, getRankingResearch } from "@/lib/server-api";

function loaded<T>(result: { status: "ok"; data: T } | { status: "not_found" } | { status: "error"; message: string }) {
  return result.status === "ok"
    ? { status: "ok" as const, data: result.data }
    : { status: "unavailable" as const, message: result.status === "error" ? result.message : "not found" };
}

export default async function ModelsPage() {
  const [ranking, knowledge, live] = await Promise.all([
    getRankingResearch(),
    getModelKnowledge(),
    getLiveRankings(),
  ]);
  return <ResearchModels ranking={loaded(ranking)} knowledge={loaded(knowledge)} live={liveModels(live)} />;
}
