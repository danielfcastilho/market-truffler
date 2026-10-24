import { ResearchOverview } from "@/components/research/research-overview";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getResearchOverview } from "@/lib/server-api";

export default async function OinkCorpPage() {
  const result = await getResearchOverview();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <ResearchOverview overview={result.data} />;
}
