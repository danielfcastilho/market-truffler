import { ResearchHistory } from "@/components/research/research-history";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getResearchHistory } from "@/lib/server-api";

export default async function ResearchHistoryPage() {
  const result = await getResearchHistory();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <ResearchHistory history={result.data} />;
}
