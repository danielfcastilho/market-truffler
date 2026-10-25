import { ModelKnowledgeView } from "@/components/research/model-knowledge";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getModelKnowledge } from "@/lib/server-api";

export default async function ModelKnowledgePage() {
  const result = await getModelKnowledge();
  if (result.status !== "ok") {
    return <ResearchUnavailable message={result.status === "error" ? result.message : "not found"} />;
  }
  return <ModelKnowledgeView knowledge={result.data} />;
}
