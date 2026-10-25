import { ResearchHypotheses } from "@/components/research/research-hypotheses";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getResearchOverview } from "@/lib/server-api";

export default async function HypothesesPage() {
  const result = await getResearchOverview();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <ResearchHypotheses overview={result.data} />;
}
