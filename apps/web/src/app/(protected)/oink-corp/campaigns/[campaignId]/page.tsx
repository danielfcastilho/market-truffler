import { notFound } from "next/navigation";

import { CampaignExplorer } from "@/components/research/campaign-explorer";
import { ResearchUnavailable } from "@/components/research/research-unavailable";
import { getResearchCampaign } from "@/lib/server-api";

export default async function CampaignPage({
  params,
}: {
  params: Promise<{ campaignId: string }>;
}) {
  const { campaignId } = await params;
  const result = await getResearchCampaign(campaignId);
  if (result.status === "not_found") notFound();
  if (result.status === "error") return <ResearchUnavailable message={result.message} />;
  return <CampaignExplorer detail={result.data} />;
}
