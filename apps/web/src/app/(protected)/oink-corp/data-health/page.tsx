import { DataHealthView } from "@/components/research/data-health";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getDataHealth } from "@/lib/server-api";

export default async function DataHealthPage() {
  const result = await getDataHealth();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <DataHealthView health={result.data} />;
}
