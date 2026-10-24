import { notFound } from "next/navigation";

import { ExperimentInspector } from "@/components/research/experiment-inspector";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getResearchExperiment } from "@/lib/server-api";

export default async function ExperimentPage({
  params,
}: {
  params: Promise<{ experimentId: string }>;
}) {
  const { experimentId } = await params;
  const result = await getResearchExperiment(experimentId);
  if (result.status === "not_found") notFound();
  if (result.status === "error") return <ResearchUnavailable message={result.message} />;
  return <ExperimentInspector detail={result.data} />;
}
